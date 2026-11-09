'use strict'

// Ponte local entre a aplicacao Python e o WhatsApp.
//
// O Baileys e uma biblioteca Node, entao o vinculo da conta e o envio ficam
// neste processo. A aplicacao conversa com ele por HTTP em 127.0.0.1: nada e
// exposto para fora da maquina.

const fs = require('fs')
const path = require('path')
const express = require('express')
const pino = require('pino')
const QRCode = require('qrcode')
const {
  default: makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
  generateMessageID,
  jidNormalizedUser,
  proto,
} = require('@whiskeysockets/baileys')

const PORT = Number(process.env.EMC_WHATSAPP_PORT || 8511)
const TOKEN = process.env.EMC_WHATSAPP_TOKEN || ''
const AUTH_DIR = process.env.EMC_WHATSAPP_AUTH_DIR
  || path.join(__dirname, '..', 'data', 'whatsapp_session')

const logger = pino({ level: process.env.EMC_WHATSAPP_LOG_LEVEL || 'warn' })

const estado = {
  conexao: 'desconectado', // desconectado | aguardando_qr | conectando | conectado
  qr: '',                  // data URL do QR atual
  telefone: '',
  ultimoErro: '',
  atualizadoEm: null,
}

let socket = null
let encerrando = false

// Buscar os grupos no servidor do WhatsApp e lento e a lista muda pouco, entao
// guardamos o resultado por um tempo curto. A tela pode pedir /groups?atualizar=1
// quando o usuario quiser forcar uma releitura.
const GRUPOS_TTL_MS = 60000
const cacheGrupos = { itens: [], atualizadoEm: 0 }

// Num grupo de avisos o cargo de administrador pertence a comunidade, nao ao
// grupo, e precisa de uma consulta propria. Como ele muda raramente, fica em
// cache separado e mais longo: `esquecerGrupos` nao pode derrubar isso, senao
// cada releitura da lista viraria uma consulta extra -- caminho curto para o
// "rate-overlimit" do servidor.
const CARGO_TTL_MS = 600000
const cacheCargo = new Map()

// O envio nao espera resposta: `relayMessage` escreve a stanza no socket e
// resolve, e o id devolvido e sorteado localmente. Quem avisa que o servidor
// recusou a mensagem e o ack com erro, que chega depois. Sem esperar por ele,
// toda mensagem descartada em silencio era relatada como enviada.
// O ack de recusa chega rapido, logo apos a stanza sair; a confirmacao de
// entrega em grupo pode levar minutos. A espera e curta de proposito: serve
// para pegar a recusa, nao para aguardar o recibo de todos os participantes.
const ESPERA_ACK_MS = 3000
const esperandoAck = new Map()

function aguardarAck(id) {
  return new Promise((resolve) => {
    const relogio = setTimeout(() => {
      esperandoAck.delete(id)
      resolve({ confirmado: false, erro: '' })
    }, ESPERA_ACK_MS)
    esperandoAck.set(id, (resultado) => {
      clearTimeout(relogio)
      esperandoAck.delete(id)
      resolve(resultado)
    })
  })
}

function avisarAck(id, resultado) {
  const aviso = esperandoAck.get(id)
  if (aviso) {
    aviso(resultado)
  }
}

// Envia e espera o veredito do servidor. O id e gerado aqui, e nao deixado
// para o Baileys, para que a espera comece antes do envio: o ack de recusa
// pode chegar no mesmo instante em que `sendMessage` resolve.
async function entregar(jid, texto) {
  const id = generateMessageID()
  const espera = aguardarAck(id)
  await socket.sendMessage(jid, { text: texto }, { messageId: id })
  const ack = await espera
  return { id, confirmado: ack.confirmado, erro: ack.erro }
}

function registrar(conexao, extra = {}) {
  estado.conexao = conexao
  estado.atualizadoEm = new Date().toISOString()
  Object.assign(estado, extra)
}

function esquecerGrupos() {
  cacheGrupos.itens = []
  cacheGrupos.atualizadoEm = 0
}

function limparSessao() {
  // Depois de um logout as credenciais gravadas ficam invalidas. Enquanto elas
  // existirem o Baileys tenta reusa-las e nunca emite um QR novo, deixando a
  // tela presa em "aguardando o codigo".
  try {
    fs.rmSync(AUTH_DIR, { recursive: true, force: true })
    console.log('[whatsapp-gateway] sessao anterior removida')
  } catch (erro) {
    console.error('[whatsapp-gateway] falha ao limpar a sessao:', erro)
  }
}

async function iniciar() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR)
  const { version } = await fetchLatestBaileysVersion()

  socket = makeWASocket({
    version,
    auth: state,
    logger,
    printQRInTerminal: false,
    // Sem esta opcao o Baileys da 60s ao primeiro QR e apenas 20s a cada QR
    // seguinte, tempo curto demais para quem precisa pegar o celular. Fixando o
    // valor, todos duram 60s -- o limite que o proprio servidor do WhatsApp
    // aceita para o codigo de pareamento.
    qrTimeout: 60000,
    browser: ['EMC Central de Novidades', 'Chrome', '1.0.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  })

  socket.ev.on('creds.update', saveCreds)

  // Entrou/saiu de um grupo ou alguem renomeou: o cache precisa ser refeito.
  socket.ev.on('groups.upsert', esquecerGrupos)
  socket.ev.on('groups.update', esquecerGrupos)
  socket.ev.on('group-participants.update', esquecerGrupos)

  // O status ERROR aqui e o servidor recusando a mensagem; e o unico sinal de
  // que um envio que nao levantou excecao nao chegou a ninguem.
  socket.ev.on('messages.update', (atualizacoes) => {
    for (const item of atualizacoes || []) {
      const id = item && item.key ? item.key.id : ''
      const atualizacao = (item && item.update) || {}
      if (!id || typeof atualizacao.status !== 'number') {
        continue
      }
      if (atualizacao.status === proto.WebMessageInfo.Status.ERROR) {
        const motivo = (atualizacao.messageStubParameters || []).join(' ')
        avisarAck(id, {
          confirmado: false,
          erro: motivo || 'recusada pelo servidor, sem motivo informado',
        })
      } else if (atualizacao.status >= proto.WebMessageInfo.Status.SERVER_ACK) {
        avisarAck(id, { confirmado: true, erro: '' })
      }
    }
  })

  // Em grupo a confirmacao nao vem como status: vem como recibo de entrega de
  // cada participante.
  socket.ev.on('message-receipt.update', (recibos) => {
    for (const item of recibos || []) {
      const id = item && item.key ? item.key.id : ''
      if (id) {
        avisarAck(id, { confirmado: true, erro: '' })
      }
    }
  })

  socket.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update

    if (qr) {
      estado.qr = await QRCode.toDataURL(qr, { margin: 1, width: 320 })
      registrar('aguardando_qr', { ultimoErro: '' })
    }

    if (connection === 'connecting') {
      registrar('conectando')
    }

    if (connection === 'open') {
      const id = socket.user && socket.user.id ? String(socket.user.id) : ''
      registrar('conectado', {
        qr: '',
        telefone: id.split(':')[0] || '',
        ultimoErro: '',
      })
    }

    if (connection === 'close') {
      esquecerGrupos()
      const codigo = lastDisconnect && lastDisconnect.error
        ? lastDisconnect.error.output?.statusCode
        : undefined
      const deslogado = codigo === DisconnectReason.loggedOut
      registrar('desconectado', {
        qr: '',
        telefone: deslogado ? '' : estado.telefone,
        ultimoErro: deslogado
          ? 'A conta foi desconectada no aparelho. Leia o QR abaixo para vincular de novo.'
          : 'Conexao encerrada; tentando reconectar.',
      })
      if (encerrando) {
        return
      }
      // Apos o logout a sessao gravada nao serve mais: e preciso apaga-la antes
      // de reiniciar, senao o Baileys insiste nela e nenhum QR e emitido.
      if (deslogado) {
        limparSessao()
      }
      setTimeout(() => iniciar().catch((erro) => {
        registrar('desconectado', { ultimoErro: String(erro.message || erro) })
      }), deslogado ? 1000 : 3000)
    }
  })
}

function autorizado(req) {
  if (!TOKEN) return true
  return req.get('X-EMC-Token') === TOKEN
}

function comoJid(numero) {
  const digitos = String(numero || '').replace(/\D+/g, '')
  if (digitos.length < 12 || digitos.length > 15) {
    throw new Error('Informe o numero com codigo do pais e DDD.')
  }
  return `${digitos}@s.whatsapp.net`
}

const JID_GRUPO = /^\d+(-\d+)?@g\.us$/

function comoJidGrupo(identificador) {
  const bruto = String(identificador || '').trim()
  if (!JID_GRUPO.test(bruto)) {
    throw new Error('Selecione um grupo valido.')
  }
  return bruto
}

// A conta tem duas identidades: o numero (@s.whatsapp.net) e o LID
// (@lid), o identificador anonimo que o WhatsApp passou a usar nos grupos.
// Qual delas aparece na lista de participantes depende do modo de
// enderecamento do grupo, entao guardamos as duas para comparar.
function minhasIdentidades() {
  const usuario = socket && socket.user ? socket.user : {}
  return new Set(
    [usuario.id, usuario.lid]
      .filter(Boolean)
      .map((valor) => jidNormalizedUser(String(valor)))
      .filter(Boolean),
  )
}

function souEu(participante, identidades) {
  // Em grupos com enderecamento LID o `id` e o proprio LID e o numero fica em
  // `jid`; em grupos por numero acontece o contrario. Comparar so um dos campos
  // faz o participante "sumir" da lista e a conta parecer nao-admin.
  return [participante.id, participante.jid, participante.lid]
    .filter(Boolean)
    .map((valor) => jidNormalizedUser(String(valor)))
    .some((valor) => valor && identidades.has(valor))
}

// Cargo na comunidade que abriga um grupo de avisos. Devolve null quando a
// consulta falha: sem saber o cargo e melhor liberar a tentativa do que barrar
// um envio legitimo.
async function souAdminDaComunidade(paiJid) {
  const guardado = cacheCargo.get(paiJid)
  const agora = Date.now()
  if (guardado && agora - guardado.atualizadoEm < CARGO_TTL_MS) {
    return guardado.admin
  }
  try {
    const pai = await socket.groupMetadata(paiJid)
    const identidades = minhasIdentidades()
    const participantes = Array.isArray(pai.participants) ? pai.participants : []
    const meuRegistro = participantes.find((p) => souEu(p, identidades))
    const admin = Boolean(
      meuRegistro && (meuRegistro.admin === 'admin' || meuRegistro.admin === 'superadmin'),
    )
    cacheCargo.set(paiJid, { admin, atualizadoEm: agora })
    return admin
  } catch (erro) {
    console.error('[whatsapp-gateway] falha ao ler a comunidade:', String(erro.message || erro))
    return null
  }
}

async function listarGrupos(forcar = false) {
  const agora = Date.now()
  if (!forcar && cacheGrupos.atualizadoEm && agora - cacheGrupos.atualizadoEm < GRUPOS_TTL_MS) {
    return cacheGrupos.itens
  }
  const metadados = await socket.groupFetchAllParticipating()
  const identidades = minhasIdentidades()
  const itens = []
  for (const grupo of Object.values(metadados || {})) {
    // A comunidade em si nao e conversa: a mensagem enviada para ela e aceita
    // pelo socket e descartada pelo servidor, sem erro algum. Como ela aparece
    // nesta consulta com o mesmo nome do grupo de avisos, ficava impossivel
    // distinguir as duas na tela -- fora da lista de destinos.
    if (grupo.isCommunity) {
      continue
    }
    const participantes = Array.isArray(grupo.participants) ? grupo.participants : []
    const meuRegistro = participantes.find((p) => souEu(p, identidades))
    let souAdmin = Boolean(
      meuRegistro && (meuRegistro.admin === 'admin' || meuRegistro.admin === 'superadmin'),
    )
    const avisos = Boolean(grupo.isCommunityAnnounce)
    // No grupo de avisos o cargo vem da comunidade: a lista local traz o
    // administrador sem marca alguma, e so a consulta ao pai revela o cargo.
    if (avisos && !souAdmin && grupo.linkedParent) {
      const cargo = await souAdminDaComunidade(grupo.linkedParent)
      if (cargo !== null) {
        souAdmin = cargo
      }
    }
    // Em grupos com "somente administradores" o envio e recusado pelo servidor,
    // entao a tela precisa saber disso antes de tentar. Quando nao conseguimos
    // nos localizar na lista nao ha como saber o cargo: liberamos a tentativa e
    // deixamos o servidor decidir, em vez de bloquear um envio talvez valido.
    const somenteAdmin = Boolean(grupo.announce)
    itens.push({
      id: grupo.id,
      nome: String(grupo.subject || '').trim() || grupo.id,
      // O grupo de avisos costuma vir sem a lista de participantes, e contar
      // essa lista dava zero: `size` e o total que o servidor informa.
      participantes: Number(grupo.size) || participantes.length,
      avisos,
      somente_admin: somenteAdmin,
      posso_enviar: !somenteAdmin || souAdmin || !meuRegistro,
    })
  }
  itens.sort((a, b) => a.nome.localeCompare(b.nome, 'pt-BR'))
  cacheGrupos.itens = itens
  cacheGrupos.atualizadoEm = agora
  return itens
}

const app = express()
app.use(express.json({ limit: '1mb' }))

app.use((req, res, next) => {
  if (!autorizado(req)) {
    return res.status(401).json({ erro: 'Token invalido.' })
  }
  next()
})

app.get('/status', (req, res) => {
  res.json({
    conexao: estado.conexao,
    conectado: estado.conexao === 'conectado',
    telefone: estado.telefone,
    tem_qr: Boolean(estado.qr),
    ultimo_erro: estado.ultimoErro,
    atualizado_em: estado.atualizadoEm,
  })
})

app.get('/qr', (req, res) => {
  if (!estado.qr) {
    return res.status(404).json({ erro: 'Nenhum QR disponivel no momento.' })
  }
  res.json({ qr: estado.qr })
})

app.get('/groups', async (req, res) => {
  if (estado.conexao !== 'conectado') {
    return res.status(409).json({ erro: 'WhatsApp nao esta conectado.' })
  }
  try {
    const grupos = await listarGrupos(req.query.atualizar === '1')
    res.json({ grupos })
  } catch (erro) {
    res.status(400).json({ erro: String(erro.message || erro) })
  }
})

app.post('/send', async (req, res) => {
  const { to, message } = req.body || {}
  const grupoId = (req.body || {}).group_id
  if (estado.conexao !== 'conectado') {
    return res.status(409).json({ erro: 'WhatsApp nao esta conectado.' })
  }
  const texto = String(message || '').trim()
  if (!texto) {
    return res.status(400).json({ erro: 'A mensagem nao pode ficar vazia.' })
  }
  if (grupoId) {
    // Grupo: o JID ja vem da listagem, entao nao ha o que consultar em onWhatsApp.
    try {
      const jid = comoJidGrupo(grupoId)
      const ack = await entregar(jid, texto)
      if (ack.erro) {
        return res.status(400).json({ erro: `O WhatsApp recusou a mensagem: ${ack.erro}` })
      }
      return res.json({ id: ack.id, para: jid, confirmado: ack.confirmado })
    } catch (erro) {
      return res.status(400).json({ erro: String(erro.message || erro) })
    }
  }
  try {
    const jid = comoJid(to)
    const [existe] = await socket.onWhatsApp(jid)
    if (!existe || !existe.exists) {
      return res.status(404).json({ erro: 'Este numero nao possui WhatsApp.' })
    }
    const ack = await entregar(existe.jid, texto)
    if (ack.erro) {
      return res.status(400).json({ erro: `O WhatsApp recusou a mensagem: ${ack.erro}` })
    }
    res.json({ id: ack.id, para: existe.jid, confirmado: ack.confirmado })
  } catch (erro) {
    res.status(400).json({ erro: String(erro.message || erro) })
  }
})

app.post('/logout', async (req, res) => {
  encerrando = true
  try {
    if (socket) {
      await socket.logout()
    }
  } catch (erro) {
    logger.warn({ erro: String(erro) }, 'falha ao desconectar')
  }
  try {
    if (socket) {
      socket.ev.removeAllListeners('connection.update')
      socket.end(undefined)
    }
  } catch (erro) {
    logger.warn({ erro: String(erro) }, 'falha ao encerrar o socket')
  }
  socket = null
  esquecerGrupos()
  limparSessao()
  registrar('desconectado', { qr: '', telefone: '', ultimoErro: '' })
  encerrando = false
  setTimeout(() => iniciar().catch((erro) => {
    registrar('desconectado', { ultimoErro: String(erro.message || erro) })
  }), 500)
  res.json({ ok: true })
})

app.listen(PORT, '127.0.0.1', () => {
  console.log(`[whatsapp-gateway] ouvindo em http://127.0.0.1:${PORT}`)
  iniciar().catch((erro) => {
    registrar('desconectado', { ultimoErro: String(erro.message || erro) })
    console.error('[whatsapp-gateway] falha ao iniciar:', erro)
  })
})

process.on('SIGINT', () => { encerrando = true; process.exit(0) })
process.on('SIGTERM', () => { encerrando = true; process.exit(0) })

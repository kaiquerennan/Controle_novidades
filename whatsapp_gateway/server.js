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
  jidNormalizedUser,
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
    browser: ['EMC Central de Novidades', 'Chrome', '1.0.0'],
    syncFullHistory: false,
    markOnlineOnConnect: false,
  })

  socket.ev.on('creds.update', saveCreds)

  // Entrou/saiu de um grupo ou alguem renomeou: o cache precisa ser refeito.
  socket.ev.on('groups.upsert', esquecerGrupos)
  socket.ev.on('groups.update', esquecerGrupos)
  socket.ev.on('group-participants.update', esquecerGrupos)

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

async function listarGrupos(forcar = false) {
  const agora = Date.now()
  if (!forcar && cacheGrupos.atualizadoEm && agora - cacheGrupos.atualizadoEm < GRUPOS_TTL_MS) {
    return cacheGrupos.itens
  }
  const metadados = await socket.groupFetchAllParticipating()
  const eu = socket.user && socket.user.id ? jidNormalizedUser(socket.user.id) : ''
  const itens = Object.values(metadados || {}).map((grupo) => {
    const participantes = Array.isArray(grupo.participants) ? grupo.participants : []
    const meuRegistro = participantes.find((p) => jidNormalizedUser(p.id || '') === eu)
    const souAdmin = Boolean(meuRegistro && (meuRegistro.admin === 'admin' || meuRegistro.admin === 'superadmin'))
    // Em grupos com "somente administradores" o envio e recusado pelo servidor,
    // entao a tela precisa saber disso antes de tentar.
    const somenteAdmin = Boolean(grupo.announce)
    return {
      id: grupo.id,
      nome: String(grupo.subject || '').trim() || grupo.id,
      participantes: participantes.length,
      somente_admin: somenteAdmin,
      posso_enviar: !somenteAdmin || souAdmin,
    }
  })
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
      const enviado = await socket.sendMessage(jid, { text: texto })
      return res.json({ id: enviado?.key?.id || '', para: jid })
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
    const enviado = await socket.sendMessage(existe.jid, { text: texto })
    res.json({ id: enviado?.key?.id || '', para: existe.jid })
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

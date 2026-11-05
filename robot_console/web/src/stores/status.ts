// 全局状态：维护到后端的 WebSocket 连接，保存最新一条状态消息。
// 页面可以订阅额外的数据流（如建图页的点云），后端只给订阅了的连接推送；
// 这些数据量大、更新快，不放进响应式状态，而是通过 addJsonHandler / addBinaryHandler 直接交给页面处理
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { StatusMsg } from '@/types'

type JsonHandler = (msg: { type: string; [k: string]: unknown }) => void
type BinaryHandler = (buf: ArrayBuffer) => void

export const useStatusStore = defineStore('status', () => {
  const connected = ref(false)
  const status = ref<StatusMsg | null>(null)
  let retryDelay = 1000
  let sock: WebSocket | null = null
  const streams = new Set<string>()
  const jsonHandlers = new Set<JsonHandler>()
  const binaryHandlers = new Set<BinaryHandler>()

  function connect() {
    if (sock) return
    const proto = location.protocol === 'https:' ? 'wss' : 'ws'
    sock = new WebSocket(`${proto}://${location.host}/ws`)
    sock.binaryType = 'arraybuffer'
    sock.onopen = () => {
      connected.value = true
      retryDelay = 1000
      sendStreams()               // 重连后重新订阅（后端会从头重发数据）
    }
    sock.onmessage = (ev) => {
      if (typeof ev.data !== 'string') {
        binaryHandlers.forEach((h) => h(ev.data as ArrayBuffer))
        return
      }
      const msg = JSON.parse(ev.data)
      if (msg.type === 'status') status.value = msg as StatusMsg
      else jsonHandlers.forEach((h) => h(msg))
    }
    sock.onclose = () => {
      // 后端没启动或重启时自动重连：1 s、2 s、4 s……最长 5 s 一次
      connected.value = false
      sock = null
      setTimeout(connect, retryDelay)
      retryDelay = Math.min(retryDelay * 2, 5000)
    }
  }

  function sendStreams() {
    if (sock?.readyState === WebSocket.OPEN) {
      sock.send(JSON.stringify({ type: 'subscribe', streams: [...streams] }))
    }
  }

  /** 订阅数据流。已经订阅时再调用一次，后端会从头重发（用于重新同步） */
  function subscribe(stream: string) {
    streams.add(stream)
    sendStreams()
  }
  function unsubscribe(stream: string) {
    if (streams.delete(stream)) sendStreams()
  }

  /** 发一条消息给后端（例如遥控速度）；没连上时丢弃，返回是否发出 */
  function send(msg: object) {
    if (sock?.readyState !== WebSocket.OPEN) return false
    sock.send(JSON.stringify(msg))
    return true
  }

  /** 注册消息处理函数，返回取消注册的函数 */
  function addJsonHandler(h: JsonHandler) {
    jsonHandlers.add(h)
    return () => jsonHandlers.delete(h)
  }
  function addBinaryHandler(h: BinaryHandler) {
    binaryHandlers.add(h)
    return () => binaryHandlers.delete(h)
  }

  /** Data folders for display, from the backend configuration (defaults until the first status arrives) */
  const paths = computed(() => status.value?.paths ?? { maps: '~/maps', runs: '~/runs' })

  return { connected, status, paths, connect, subscribe, unsubscribe, send, addJsonHandler, addBinaryHandler }
})

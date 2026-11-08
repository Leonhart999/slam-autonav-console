// 显示格式工具函数

/** 秒数 → "1:02:03" 或 "2:03" */
export function fmtDuration(sec: number | null | undefined): string {
  if (sec == null) return '—'
  const s = Math.floor(sec)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const ss = String(s % 60).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

/** 消息类型去掉中间的 msg：sensor_msgs/msg/PointCloud2 → sensor_msgs/PointCloud2 */
export function shortType(t: string | null): string {
  return t ? t.replace('/msg/', '/') : '—'
}

/** 大数字缩写：1234 → "1.2k"，2500000 → "2.50M" */
export function fmtCount(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1e6) return `${(n / 1e3).toFixed(1)}k`
  return `${(n / 1e6).toFixed(2)}M`
}

/** 字节数 → "3.1 GB"、"269 MB"、"12 kB"（十进制单位，和文件管理器一致） */
export function fmtBytes(bytes: number): string {
  if (bytes >= 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes >= 1e6) return `${(bytes / 1e6).toFixed(0)} MB`
  return `${Math.max(1, Math.round(bytes / 1e3))} kB`
}

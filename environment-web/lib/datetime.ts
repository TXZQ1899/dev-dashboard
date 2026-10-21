// 前端统一的日期时间格式化工具。
// 约定：所有需要展示时间的界面都通过本模块，保证始终按北京时间（UTC+8）展示。
// 不要直接对 ISO 字符串做 slice/replace——采集数据里存在 UTC（+00:00）时间戳，
// 直接截断会把 UTC 当成北京时间展示，导致 8 小时偏差。

export const BEIJING_TIME_ZONE = 'Asia/Shanghai';
const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000;

export type BeijingTimeStyle = 'date' | 'minute' | 'second';

// 无时区的 "YYYY-MM-DD HH:MM(:SS)"：按北京时间墙钟解析。
const NAIVE_TIME = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(:\d{2})?$/;
// 版本/快照 ID 内嵌的 "YYYYMMDD-HHMMSS[-ffffff]"。
const SNAPSHOT_ID = /(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})(?:-\d+)?$/;

// 把输入解析成毫秒时间戳：ISO/RFC 串交给 Date.parse（自带时区），
// 无时区串按北京时间，毫秒数字与 Date 直接取。无法解析返回 null。
export function parseBeijingTime(value: string | number | Date | null | undefined): number | null {
  if (value === null || value === undefined || value === '') return null;
  if (value instanceof Date) return Number.isFinite(value.getTime()) ? value.getTime() : null;
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  const text = String(value).trim();
  if (!text) return null;
  const naive = NAIVE_TIME.exec(text);
  const parsed = Date.parse(naive ? `${naive[1]}T${naive[2]}${naive[3] ?? ':00'}+08:00` : text);
  return Number.isFinite(parsed) ? parsed : null;
}

function pad(n: number): string {
  return String(n).padStart(2, '0');
}

// 按北京时间格式化，风格：date → "YYYY-MM-DD"；minute → "YYYY-MM-DD HH:MM"；second → "YYYY-MM-DD HH:MM:SS"。
// 解析失败返回 fallback。
export function formatBeijing(
  value: string | number | Date | null | undefined,
  style: BeijingTimeStyle = 'second',
  fallback = '',
): string {
  const ts = parseBeijingTime(value);
  if (ts === null) return fallback;
  const d = new Date(ts + BEIJING_OFFSET_MS);
  const date = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  if (style === 'date') return date;
  const time = style === 'minute' ? `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` : `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  return `${date} ${time}`;
}

export function formatBeijingTime(value: string | number | Date | null | undefined, fallback = ''): string {
  return formatBeijing(value, 'second', fallback);
}

export function formatBeijingMinute(value: string | number | Date | null | undefined, fallback = ''): string {
  return formatBeijing(value, 'minute', fallback);
}

export function formatBeijingDate(value: string | number | Date | null | undefined, fallback = ''): string {
  return formatBeijing(value, 'date', fallback);
}

// 版本/快照 ID（如 "20261019-205400-790994" 或 "topology-20261019-205400-790994"）。
// 内嵌时间戳本身就是北京时间墙钟（已核对 version.json 的 createdAt 与版本目录 mtime 一致），
// 因此直接按北京时间展示，不再做时区偏移。无法识别时返回 fallback。
export function formatSnapshotId(id: string | null | undefined, style: BeijingTimeStyle = 'second', fallback = '—'): string {
  if (!id) return fallback;
  const m = SNAPSHOT_ID.exec(String(id));
  if (!m) return fallback;
  const date = `${m[1]}-${m[2]}-${m[3]}`;
  if (style === 'date') return date;
  return style === 'minute' ? `${date} ${m[4]}:${m[5]}` : `${date} ${m[4]}:${m[5]}:${m[6]}`;
}

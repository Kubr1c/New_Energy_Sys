import DOMPurify from 'dompurify'
import { marked } from 'marked'

const SAFE_URI = /^(?:(?:https?|mailto|tel):|[^a-z]|[a-z+.-]+(?:[^a-z+.-:]|$))/i

/**
 * 将实验报告 Markdown 渲染为经过清洗的 HTML。
 *
 * `marked` 只负责 Markdown -> HTML，不提供 XSS 防护；所有进入 `v-html`
 * 的内容必须经过 DOMPurify。这里保留常用报告标签，同时显式禁止脚本、
 * iframe、object、embed 和事件属性，避免本地报告来源在后续扩展为上传、
 * 拉取或自动生成内容时变成长期安全边界。
 */
export function renderMarkdown(mdContent) {
  if (!mdContent) return ''

  const html = marked.parse(mdContent, {
    async: false,
    gfm: true,
    breaks: false,
  })

  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ['script', 'iframe', 'object', 'embed'],
    FORBID_ATTR: ['style'],
    ALLOWED_URI_REGEXP: SAFE_URI,
  })
}

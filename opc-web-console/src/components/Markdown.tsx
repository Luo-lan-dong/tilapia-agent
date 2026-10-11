/**
 * 轻量 Markdown 渲染器（零依赖，ADR-049 前端不引入 markdown 库的替代实现）。
 *
 * 设计要点：把 Markdown 解析为 **React 元素**而非 HTML 字符串——所有文本都经
 * React 文本节点渲染、绝不使用 `dangerouslySetInnerHTML`，因此天然免疫 XSS：
 * Agent 产物或模型正文中若混入 `<script>` / `<img onerror=...>` 等，只会作为
 * 字面量显示。这也是本项目不引入第三方 markdown + 消毒插件的原因。
 *
 * 支持语法（够用为度，按需扩展）：
 * - 标题 `#`~`######`
 * - 围栏代码块 ``` ```lang ```（语言标签仅作展示）
 * - 无序列表 `-` / `*` / `+`、有序列表 `1.`
 * - 引用 `>`
 * - 分割线 `---` / `***` / `___`
 * - 行内：`**粗体**`、`*斜体*`、`` `代码` ``、`[文字](url)`
 *
 * 不支持（已知限制，见 docs/spec/artifacts-panel-ui-changes.md）：表格、嵌套列表、
 * 任务列表、脚注、HTML 透传。
 */
import type { ReactNode } from 'react';

import './Markdown.css';

/** 允许的链接协议（防 `javascript:` 伪协议） */
const SAFE_LINK_PROTOCOLS = ['http://', 'https://', 'mailto:'];

interface TextToken {
  type: 'text';
  value: string;
}
interface CodeToken {
  type: 'code';
  value: string;
}
interface StrongToken {
  type: 'strong';
  value: string;
}
interface EmToken {
  type: 'em';
  value: string;
}
interface LinkToken {
  type: 'link';
  value: string;
  href: string;
}

type InlineToken = TextToken | CodeToken | StrongToken | EmToken | LinkToken;

/** 行内语法正则（顺序即优先级：行内码 > 链接 > 粗体 > 斜体） */
const INLINE_PATTERN =
  /(`[^`\n]+`)|(\[[^\]\n]*\]\([^)\s]+\))|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*)/g;

/**
 * 判断链接是否安全（仅放行 http/https/mailto；相对路径按原样允许）。
 */
function isSafeHref(href: string): boolean {
  const lowered = href.trim().toLowerCase();
  if (lowered.startsWith('/') || lowered.startsWith('#')) {
    return true;
  }
  return SAFE_LINK_PROTOCOLS.some((protocol) => lowered.startsWith(protocol));
}

/**
 * 把一行文本切分为行内 token 序列。
 */
function tokenizeInline(line: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let cursor = 0;
  INLINE_PATTERN.lastIndex = 0;
  let match = INLINE_PATTERN.exec(line);
  while (match !== null) {
    if (match.index > cursor) {
      tokens.push({ type: 'text', value: line.slice(cursor, match.index) });
    }
    const raw = match[0];
    if (raw.startsWith('`')) {
      tokens.push({ type: 'code', value: raw.slice(1, -1) });
    } else if (raw.startsWith('[')) {
      const split = raw.indexOf('](');
      tokens.push({
        type: 'link',
        value: raw.slice(1, split),
        href: raw.slice(split + 2, -1),
      });
    } else if (raw.startsWith('**')) {
      tokens.push({ type: 'strong', value: raw.slice(2, -2) });
    } else {
      tokens.push({ type: 'em', value: raw.slice(1, -1) });
    }
    cursor = match.index + raw.length;
    match = INLINE_PATTERN.exec(line);
  }
  if (cursor < line.length) {
    tokens.push({ type: 'text', value: line.slice(cursor) });
  }
  return tokens;
}

/** 行内 token 序列 → React 节点（文本一律走 text node，无 HTML 注入面） */
function renderInline(line: string, keyPrefix: string): ReactNode[] {
  return tokenizeInline(line).map((token, index) => {
    const key = `${keyPrefix}-i${index}`;
    switch (token.type) {
      case 'code':
        return (
          <code key={key} className="md-inline-code">
            {token.value}
          </code>
        );
      case 'strong':
        return <strong key={key}>{token.value}</strong>;
      case 'em':
        return <em key={key}>{token.value}</em>;
      case 'link':
        return isSafeHref(token.href) ? (
          <a key={key} href={token.href} target="_blank" rel="noreferrer noopener">
            {token.value}
          </a>
        ) : (
          // 不安全协议（如 javascript:）不生成 <a>，按字面量显示
          <span key={key}>{`[${token.value}](${token.href})`}</span>
        );
      default:
        return <span key={key}>{token.value}</span>;
    }
  });
}

const HEADING_PATTERN = /^(#{1,6})\s+(.*)$/;
const FENCE_PATTERN = /^\s*(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$/;
const UNORDERED_PATTERN = /^\s*[-*+]\s+(.*)$/;
const ORDERED_PATTERN = /^\s*\d+[.)]\s+(.*)$/;
const QUOTE_PATTERN = /^\s*>\s?(.*)$/;
const HR_PATTERN = /^\s*(-{3,}|\*{3,}|_{3,})\s*$/;

/**
 * 渲染 Markdown 文本为 React 节点。
 *
 * @param text Markdown 源文本（可为空）
 * @returns React 节点数组（空文本返回 null 由调用方处理）
 */
export function renderMarkdown(text: string): ReactNode {
  if (!text) {
    return null;
  }
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let index = 0;
  let blockSeq = 0;

  while (index < lines.length) {
    const line = lines[index];
    const key = `md-b${blockSeq}`;
    blockSeq += 1;

    // 围栏代码块
    const fence = FENCE_PATTERN.exec(line);
    if (fence) {
      const marker = fence[1][0];
      const language = fence[2];
      const body: string[] = [];
      index += 1;
      while (index < lines.length) {
        const candidate = lines[index];
        if (FENCE_PATTERN.test(candidate) && candidate.trim().startsWith(marker)) {
          index += 1;
          break;
        }
        body.push(candidate);
        index += 1;
      }
      blocks.push(
        <pre key={key} className="md-code-block" data-language={language || undefined}>
          <code>{body.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    // 空行
    if (!line.trim()) {
      index += 1;
      continue;
    }

    // 分割线（必须在列表之前判断，避免 `---` 被当作列表项）
    if (HR_PATTERN.test(line)) {
      blocks.push(<hr key={key} className="md-hr" />);
      index += 1;
      continue;
    }

    // 标题
    const heading = HEADING_PATTERN.exec(line);
    if (heading) {
      const level = heading[1].length;
      const content = heading[2];
      const Tag = `h${level}` as 'h1' | 'h2' | 'h3' | 'h4' | 'h5' | 'h6';
      blocks.push(
        <Tag key={key} className={`md-heading md-heading--${level}`}>
          {renderInline(content, key)}
        </Tag>,
      );
      index += 1;
      continue;
    }

    // 引用（连续行合并为一个引用块）
    if (QUOTE_PATTERN.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && QUOTE_PATTERN.test(lines[index])) {
        quoteLines.push(QUOTE_PATTERN.exec(lines[index])?.[1] ?? '');
        index += 1;
      }
      blocks.push(
        <blockquote key={key} className="md-quote">
          {quoteLines.map((quoteLine, quoteIndex) => (
            <p key={`${key}-q${quoteIndex}`}>{renderInline(quoteLine, `${key}-q${quoteIndex}`)}</p>
          ))}
        </blockquote>,
      );
      continue;
    }

    // 有序 / 无序列表（同类型连续行合并）
    const ordered = ORDERED_PATTERN.test(line);
    if (ordered || UNORDERED_PATTERN.test(line)) {
      const pattern = ordered ? ORDERED_PATTERN : UNORDERED_PATTERN;
      const items: string[] = [];
      while (index < lines.length) {
        const itemMatch = pattern.exec(lines[index]);
        if (!itemMatch) {
          break;
        }
        items.push(itemMatch[1]);
        index += 1;
      }
      const ListTag = ordered ? 'ol' : 'ul';
      blocks.push(
        <ListTag key={key} className="md-list">
          {items.map((item, itemIndex) => (
            <li key={`${key}-l${itemIndex}`}>{renderInline(item, `${key}-l${itemIndex}`)}</li>
          ))}
        </ListTag>,
      );
      continue;
    }

    // 段落（连续非空行合并，保留软换行）
    const paragraphLines: string[] = [];
    while (index < lines.length) {
      const candidate = lines[index];
      if (
        !candidate.trim() ||
        FENCE_PATTERN.test(candidate) ||
        HEADING_PATTERN.test(candidate) ||
        QUOTE_PATTERN.test(candidate) ||
        HR_PATTERN.test(candidate) ||
        ORDERED_PATTERN.test(candidate) ||
        UNORDERED_PATTERN.test(candidate)
      ) {
        break;
      }
      paragraphLines.push(candidate);
      index += 1;
    }
    blocks.push(
      <p key={key} className="md-paragraph">
        {paragraphLines.map((paragraphLine, lineIndex) => (
          <span key={`${key}-p${lineIndex}`}>
            {lineIndex > 0 && <br />}
            {renderInline(paragraphLine, `${key}-p${lineIndex}`)}
          </span>
        ))}
      </p>,
    );
  }

  return <div className="md-body">{blocks}</div>;
}

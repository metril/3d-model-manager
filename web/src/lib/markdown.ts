/**
 * Tiny markdown renderer for notes (Task 8 decision: `marked` + DOMPurify
 * sanitize, no larger markdown/editor dependency).
 */
import { sanitizeHtml } from "@/lib/sanitize";
import { marked } from "marked";

marked.setOptions({ gfm: true, breaks: true });

/** Render markdown to sanitized HTML safe to drop into `dangerouslySetInnerHTML`. */
export function renderMarkdown(source: string): string {
  const html = marked.parse(source, { async: false });
  return sanitizeHtml(html);
}

/** Utility classes giving rendered markdown reasonable typography without
 * pulling in `@tailwindcss/typography`. */
export const MARKDOWN_CLASSNAME =
  "text-sm leading-relaxed [&_p]:mb-2 [&_p:last-child]:mb-0 [&_ul]:list-disc [&_ul]:pl-5 " +
  "[&_ol]:list-decimal [&_ol]:pl-5 [&_a]:text-primary [&_a]:underline [&_code]:rounded " +
  "[&_code]:bg-muted [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-xs [&_pre]:overflow-x-auto " +
  "[&_pre]:rounded [&_pre]:bg-muted [&_pre]:p-2 [&_h1]:text-base [&_h1]:font-semibold " +
  "[&_h2]:text-sm [&_h2]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:border-border " +
  "[&_blockquote]:pl-3 [&_blockquote]:text-muted-foreground";

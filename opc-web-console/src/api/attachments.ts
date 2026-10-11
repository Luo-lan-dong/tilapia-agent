/**
 * 会话附件 API（ADR-039）：图片上传 / 会话附件预览地址。
 */
import { request } from './request';

/**
 * 附件元数据（服务端返回）。
 */
export interface SessionAttachmentItem {
  id: string;
  file_name: string;
  mime: string;
  size: number;
  kind: 'image' | 'file' | 'archive';
  created_at?: string;
}

/**
 * 上传附件选项。
 */
export interface UploadAttachmentOptions {
  /** 上传进度回调（0-100），基于 axios onUploadProgress */
  onProgress?: (percent: number) => void;
  /** 取消信号：中止上传 */
  signal?: AbortSignal;
}

/**
 * 上传会话附件（multipart/form-data）。
 *
 * 仅支持 png/jpg/webp/gif 与 zip，单文件图片 ≤10MB、zip ≤200MB（后端校验）。
 * 大文件（zip 200MB 级）弱网下耗时较长，超时放宽至 5 分钟。
 */
export function uploadSessionAttachment(
  sessionId: string,
  file: File,
  options?: UploadAttachmentOptions,
): Promise<SessionAttachmentItem> {
  const formData = new FormData();
  formData.append('upload', file);
  return request.post<SessionAttachmentItem>(
    `/v1/sessions/${sessionId}/attachments`,
    formData,
    {
      timeout: 300_000,
      signal: options?.signal,
      onUploadProgress: (event: { loaded: number; total?: number }) => {
        if (options?.onProgress && event.total) {
          options.onProgress(Math.round((event.loaded / event.total) * 100));
        }
      },
    },
  );
}

/**
 * 构造会话附件原图预览地址（历史消息图片展示）。
 */
export function sessionAttachmentPreviewUrl(sessionId: string, attachmentId: string): string {
  return `/api/v1/sessions/${sessionId}/attachments/${attachmentId}/content`;
}

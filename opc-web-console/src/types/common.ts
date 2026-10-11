/**
 * 后端通用分页元信息（与 opc-central-dispatch-hub schemas/common.py PaginationInfo 对齐）
 */
export interface PaginationMeta {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
}

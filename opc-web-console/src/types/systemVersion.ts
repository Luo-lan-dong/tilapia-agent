/**
 * 系统版本模型
 */
export interface SystemVersion {
  /** 版本记录 ID */
  id: string;
  /** 版本号，如 v0.1.0 */
  version: string;
  /** 版本标题 */
  title: string;
  /** 更新内容（纯文本，按行展示） */
  content: string;
  /** 发布日期（YYYY-MM-DD） */
  release_date: string;
  /** 是否当前版本 */
  is_current: boolean;
}

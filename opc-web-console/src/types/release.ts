/** 产品版本发布与中枢检查更新类型（ADR-026）。 */

export interface ReleaseItem {
  product: string;
  version: string;
  title: string;
  content: string;
  release_date: string | null;
  download_url: string | null;
}

export interface ProductGroup {
  product: string;
  label: string;
  releases: ReleaseItem[];
}

export interface ReleasesData {
  products: ProductGroup[];
  error: string | null;
}

export interface HubUpdateCheckResult {
  status: 'update-available' | 'up-to-date' | 'failed';
  latest_version?: string | null;
  current_version?: string | null;
  title?: string | null;
  content?: string | null;
  download_url?: string | null;
  message?: string | null;
}

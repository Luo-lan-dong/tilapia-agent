/**
 * LLM Provider 配置模型
 */
export interface LLMProviderConfig {
  provider: string;
  api_key: string;
  base_url: string;
  model: string;
  is_active: boolean;
}

/**
 * LLM Provider 配置更新请求
 */
export interface LLMProviderUpdateRequest {
  api_key: string;
  base_url: string;
  model: string;
}

/**
 * 自定义模型条目（ADR-030：OpenAI 兼容端点，api_key 已脱敏）
 */
export interface LLMModelItem {
  id: string;
  provider: string;
  model: string;
  base_url: string;
  api_key: string;
  is_active: boolean;
  /** 是否支持图片输入（ADR-039） */
  supports_vision: boolean;
  /** 模型上下文窗口 token（ADR-041） */
  context_window: number;
}

/**
 * 添加自定义模型请求
 */
export interface LLMModelCreateRequest {
  provider: string;
  model: string;
  base_url: string;
  api_key: string;
  /** 是否支持图片输入（ADR-039） */
  supports_vision?: boolean;
  /** 模型上下文窗口 token（ADR-041） */
  context_window?: number;
}

/**
 * 编辑自定义模型请求（ADR-041）
 */
export interface LLMModelUpdateRequest {
  /** 是否支持图片输入（ADR-039） */
  supports_vision?: boolean;
  /** 模型上下文窗口 token（ADR-041） */
  context_window?: number;
}

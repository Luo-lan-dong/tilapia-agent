import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from 'axios';

/**
 * 后端统一响应结构
 */
export interface ApiResponse<T = unknown> {
  code: number;
  message: string;
  data: T;
  request_id: string;
}

/**
 * 携带业务错误码上下文的异常对象。
 *
 * 相比裸 `Error`，保留了 `code` 与 `request_id`，调用方可按错误码
 * 分支处理（见 api-standards §3 错误码规范），并便于链路追踪。
 */
export class ApiError extends Error {
  /** 业务错误码，非 0 表示业务错误 */
  code: number;
  /** 请求链路 ID，用于后端日志追踪 */
  requestId: string;

  constructor(message: string, code: number, requestId: string) {
    super(message);
    this.name = 'ApiError';
    this.code = code;
    this.requestId = requestId;
  }
}

/**
 * 业务错误码为 0 时表示成功
 */
const SUCCESS_CODE = 0;

/**
 * 默认请求超时时间（毫秒）
 */
const DEFAULT_TIMEOUT_MS = 10000;

const axiosInstance: AxiosInstance = axios.create({
  baseURL: (import.meta.env.VITE_API_BASE_URL as string | undefined) || '/api',
  timeout: DEFAULT_TIMEOUT_MS,
});

axiosInstance.interceptors.response.use(
  (response: AxiosResponse<ApiResponse<unknown>>) => {
    // 204 No Content 或空响应体直接透传，避免空对象被误判为业务错误
    const rawBody = response.data as unknown;
    if (response.status === 204 || rawBody === '' || rawBody === null) {
      return response as AxiosResponse<unknown>;
    }

    const apiResponse = response.data;

    if (apiResponse.code !== SUCCESS_CODE) {
      // 携带错误码与链路 ID，调用方可按 code 分支处理（规范 §6 错误处理）
      return Promise.reject(
        new ApiError(apiResponse.message, apiResponse.code, apiResponse.request_id)
      );
    }

    // 拦截器将外层统一格式剥离，只保留业务数据，同时保持 AxiosResponse 结构
    return { ...response, data: apiResponse.data } as AxiosResponse<unknown>;
  },
  (error) => {
    // HTTP 4xx/5xx：后端仍返回统一响应格式，解析响应体保留业务 message 与错误码
    const responseData = error.response?.data as ApiResponse<unknown> | undefined;
    if (
      responseData &&
      typeof responseData.code === 'number' &&
      responseData.code !== SUCCESS_CODE
    ) {
      return Promise.reject(
        new ApiError(responseData.message, responseData.code, responseData.request_id)
      );
    }
    return Promise.reject(error);
  }
);

/**
 * 类型安全的 HTTP 请求封装。
 *
 * 通过响应拦截器处理后，所有方法均直接返回业务数据 `data`，
 * 无需在每个调用方重复处理 `{code, message, data, request_id}`。
 */
export const request = {
  get<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.get<T>(url, config).then((response) => response.data);
  },

  post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.post<T>(url, data, config).then((response) => response.data);
  },

  put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.put<T>(url, data, config).then((response) => response.data);
  },

  patch<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.patch<T>(url, data, config).then((response) => response.data);
  },

  delete<T>(url: string, config?: AxiosRequestConfig): Promise<T> {
    return axiosInstance.delete<T>(url, config).then((response) => response.data);
  },
};

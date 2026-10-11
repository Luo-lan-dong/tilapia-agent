import { setupServer } from 'msw/node';
import { handlers } from './handlers';

/**
 * MSW 测试服务器实例。
 *
 * 在 setup.ts 中已全局启用；单独导出供 API 测试覆盖特定 handler。
 */
export const server = setupServer(...handlers);

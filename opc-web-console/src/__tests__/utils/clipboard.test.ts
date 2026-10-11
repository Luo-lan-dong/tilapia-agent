import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { copyToClipboard } from '@/utils/clipboard';

describe('copyToClipboard', () => {
  const mockMessage = {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  } as unknown as Parameters<typeof copyToClipboard>[2];

  const originalClipboard = navigator.clipboard;
  const originalExecCommand = document.execCommand;

  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, 'isSecureContext', {
      value: true,
      configurable: true,
    });
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: vi.fn() },
      configurable: true,
    });
    document.execCommand = vi.fn();
  });

  afterEach(() => {
    Object.defineProperty(navigator, 'clipboard', {
      value: originalClipboard,
      configurable: true,
    });
    document.execCommand = originalExecCommand;
  });

  it('空文本时给出警告', async () => {
    await copyToClipboard('', '安装命令', mockMessage);
    expect(mockMessage.warning).toHaveBeenCalledWith('没有可复制的内容');
  });

  it('优先使用 navigator.clipboard 复制', async () => {
    (navigator.clipboard.writeText as ReturnType<typeof vi.fn>).mockResolvedValueOnce(undefined);

    await copyToClipboard('curl example.com', '安装命令', mockMessage);

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('curl example.com');
    expect(mockMessage.success).toHaveBeenCalledWith('安装命令已复制');
  });

  it('navigator.clipboard 失败时降级到 execCommand', async () => {
    (navigator.clipboard.writeText as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('denied'));
    (document.execCommand as ReturnType<typeof vi.fn>).mockReturnValueOnce(true);

    await copyToClipboard('curl example.com', '安装命令', mockMessage);

    expect(document.execCommand).toHaveBeenCalledWith('copy');
    expect(mockMessage.success).toHaveBeenCalledWith('安装命令已复制');
  });

  it('navigator.clipboard 不可用时使用 execCommand', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      configurable: true,
    });
    (document.execCommand as ReturnType<typeof vi.fn>).mockReturnValueOnce(true);

    await copyToClipboard('curl example.com', '安装命令', mockMessage);

    expect(document.execCommand).toHaveBeenCalledWith('copy');
    expect(mockMessage.success).toHaveBeenCalledWith('安装命令已复制');
  });

  it('所有复制方式都失败时提示手动复制', async () => {
    (navigator.clipboard.writeText as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error('denied'));
    (document.execCommand as ReturnType<typeof vi.fn>).mockReturnValueOnce(false);

    await copyToClipboard('curl example.com', '安装命令', mockMessage);

    expect(mockMessage.error).toHaveBeenCalledWith('复制失败，请手动复制');
  });
});

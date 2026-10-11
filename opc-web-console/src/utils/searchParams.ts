/**
 * 生成 react-router `setSearchParams` 所需的函数式更新器。
 *
 * 用于收敛「复制 prev → 局部修改 → 返回 next」的重复模板
 * （TaskNew 页面多处 URL 参数操作）。
 */
export function patchSearchParams(
  mutate: (params: URLSearchParams) => void,
): (prev: URLSearchParams) => URLSearchParams {
  return (prev) => {
    const next = new URLSearchParams(prev);
    mutate(next);
    return next;
  };
}

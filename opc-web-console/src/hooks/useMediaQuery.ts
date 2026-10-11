import { useEffect, useState } from 'react';

/**
 * 监听 CSS 媒体查询的 Hook。
 *
 * @param query CSS 媒体查询字符串，例如 '(max-width: 767px)'
 * @returns 当前查询是否匹配
 */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => {
    if (typeof window === 'undefined') {
      return false;
    }
    return window.matchMedia(query).matches;
  });

  useEffect(() => {
    const media = window.matchMedia(query);
    const handler = (event: MediaQueryListEvent) => {
      setMatches(event.matches);
    };

    media.addEventListener('change', handler);
    return () => {
      media.removeEventListener('change', handler);
    };
  }, [query]);

  return matches;
}

export type BusinessTab = 'group' | 'travel' | 'other';

export function businessTab(domain: string | null): BusinessTab {
  return !domain || domain === '未分类' ? 'other' : domain === '商旅' ? 'travel' : 'group';
}

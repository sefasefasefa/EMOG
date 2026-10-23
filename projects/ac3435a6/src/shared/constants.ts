/**
 * Renk teması breakpoints - Tailwind CSS konfigürasyonu
 */
export const colors = {
  primary: 'hsl(220, 95%, 55%)',
  secondary: 'hsl(210, 50%, 50%)',
  background: 'hsl(0, 0%, 100%)',
  surface: 'hsl(0, 0%, 98%)',
  card: 'hsl(0, 0%, 100%)',
  text: 'hsl(210, 20%, 15%)',
  textMuted: 'hsl(210, 20%, 45%)',
  border: 'hsl(210, 20%, 85%)',
  error: 'hsl(0, 80%, 55%)',
  success: 'hsl(140, 50%, 40%)',
  warning: 'hsl(45, 80%, 55%)',
};

/**
 * Breakpoint'lar - Mobile-first yaklaşım
 */
export const breakpoints = {
  sm: '640px', // Mobil
  md: '768px', // Tablet
  lg: '1024px', // Desktop
  xl: '1280px', // Büyük desktop
  '2xl': '1536px', // Çok büyük
};

/**
 * Transition süreleri
 */
export const transitions = {
  fast: '150ms',
  normal: '250ms',
  slow: '400ms',
  faster: '100ms',
};

/**
 * Z-index katmanları
 */
export const zIndex = {
  base: 0,
  overlay: 10,
  modal: 100,
  dropdown: 1000,
  sticky: 1000,
  tooltip: 10000,
};

/**
 * LocalStorage anahtarları
 */
export const localStorageKeys = {
  userPreferences: 'habrehive_user_preferences',
  bookmarkedNews: 'habrehive_bookmarked_news',
  lastScrollPosition: 'habrehive_last_scroll_position',
};

/**
 * API endpoint'lar
 */
export const apiEndpoints = {
  news: '/api/news',
  categories: '/api/categories',
  search: '/api/search',
  user: '/api/user',
};

/**
 * Feature flags / Feature flags
 */
export const featureFlags = {
  infiniteScroll: true,
  darkMode: true,
  search: true,
  bookmark: true,
  like: true,
};
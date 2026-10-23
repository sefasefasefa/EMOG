/**
 * Intersection Observer seçenekleri için yardımcı fonksiyon
 * Lazy loading için elementleri izle
 */
export const createObserver = (options?: IntersectionObserverInit) => {
  const defaultOptions: IntersectionObserverInit = {
    root: null,
    rootMargin: '0px',
    threshold: 0.01,
    ...options,
  };

  return defaultOptions;
};

/**
 * Elementlerin Intersection Observer ile izlenmesini ayarlar
 * @param callback - Görünür olduğunda tetiklenecek callback
 * @param options - Observer seçenekleri
 * @returns IntersectionObserver instance
 */
export const setupObserver = (
  callback: (entries: IntersectionObserverEntry[]) => void,
  options?: IntersectionObserverInit
): IntersectionObserver => {
  const observerOptions = createObserver(options);
  return new IntersectionObserver(callback, observerOptions);
};

/**
 * Schema.org NewsArticle structured data generate et
 * @param news - Haber verisi
 * @returns JSON-LD structured data objesi
 */
export const generateSchemaNewsArticle = (news: News): string => {
  const article: SchemaNewsArticle = {
    '@context': 'https://schema.org',
    '@type': 'NewsArticle',
    headline: news.title,
    image: news.image,
    datePublished: news.created_at,
    author: {
      '@type': 'Organization',
      name: news.author,
    },
    publisher: {
      '@type': 'Organization',
      name: 'HaberHive',
      logo: {
        '@type': 'ImageObject',
        url: '/logo.svg',
      },
    },
    description: news.excerpt,
  };

  return JSON.stringify(article);
};

/**
 * Haber kartı için CSS class'lerini birleştirir
 * @param classNames - Birleştirilecek class isimleri
 * @returns Birleştirilmiş class string
 */
export const cn = (...classes: string[]) => {
  return classes.filter(Boolean).join(' ');
};

/**
 * Formatlanmış tarih stringi oluştur
 * @param dateString - ISO date string
 * @returns Formatlanmış tarih (gün ay yıl)
 */
export const formatDate = (dateString: string): string => {
  const date = new Date(dateString);
  return date.toLocaleDateString('tr-TR', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  });
};

/**
 * Okunma süresi tahmini hesapla (karakter sayısına göre)
 * @param content - Haber içeriği
 * @returns Tahmini okuma süresi (dakika)
 */
export const readingTime = (content: string): string => {
  const wordsPerMinute = 200;
  const wordCount = content.split(/\s+/).length;
  const minutes = Math.max(1, Math.ceil(wordCount / wordsPerMinute));
  return `${minutes} dk okuma`;
};

/**
 * URL'den kategori filtrele
 * @param url - Filtrelenecek URL
 * @returns Kategori slug veya undefined
 */
export const extractCategoryFromUrl = (url: string): string | undefined => {
  const parsed = new URL(url);
  const searchParams = new URLSearchParams(parsed.search);
  return searchParams.get('category');
};

/**
 * URL'e kategori parametresi ekle
 * @param baseUrl - Temel URL
 * @param category - Eklenecek kategori
 * @returns Güncelledilmiş URL
 */
export const addCategoryToUrl = (baseUrl: string, category: string): string => {
  const url = new URL(baseUrl);
  url.searchParams.set('category', category);
  return url.toString();
};

/**
 * Arama sorgusunu URL'den çıkar
 * @param url - Aranacak URL
 * @returns Arama sorgusu veya undefined
 */
export const extractSearchQueryFromUrl = (url: string): string | undefined => {
  const parsed = new URL(url);
  return parsed.searchParams.get('q');
};
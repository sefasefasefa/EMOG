/**
 * News verisi tipi - Haber kartları için
 */
export interface News {
  id: string;
  title: string;
  slug: string;
  excerpt: string;
  content: string;
  image: string;
  category: string;
  author: string;
  created_at: string;
  updated_at: string;
  source_url: string;
}

/**
 * User verisi tipi - Kullanıcı profili ve tercihleri için
 */
export interface User {
  id: string;
  username: string;
  email: string;
  bookmarked_news_ids: string[];
  preferences: {
    categories: string[];
    dark_mode: boolean;
  };
}

/**
 * Interaction verisi tipi - Beğeni ve bookmark etkileşimleri için
 */
export interface Interaction {
  id: string;
  user_id: string;
  news_id: string;
  type: 'like' | 'bookmark';
  created_at: string;
}

/**
 * News API Response tipi - Backend/API yanıtları için
 */
export interface NewsApiResponse {
  news: News[];
  total: number;
  page: number;
  limit: number;
}

/**
 * Search Filter tipi - Kategori ve arama filtreleri için
 */
export interface NewsFilter {
  category?: string;
  searchQuery?: string;
  sortBy?: 'newest' | 'oldest' | 'most-liked';
}

/**
 * Pagination tipi - Infinite scroll pagination için
 */
export interface NewsPagination {
  skip: number;
  limit: number;
  hasMore: boolean;
}

/**
 * Schema.org NewsArticle structured data tipi
 */
export interface SchemaNewsArticle {
  '@context': 'https://schema.org';
  '@type': 'NewsArticle';
  headline: string;
  image: string;
  datePublished: string;
  author: {
    '@type': 'Organization';
    name: string;
  };
  publisher: {
    '@type': 'Organization';
    name: string;
    logo: {
      '@type': 'ImageObject';
      url: string;
    };
  };
  description: string;
}
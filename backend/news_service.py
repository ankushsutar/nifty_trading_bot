import requests
import threading
import feedparser
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
from utils.logger import logger
import time
import re

class NewsService:
    _instance = None
    
    # RSS Feeds (MoneyControl, ET, Mint)
    RSS_FEEDS = [
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms", # ET Markets
        "https://www.moneycontrol.com/rss/MCtopnews.xml", # MoneyControl Top News
        "https://www.livemint.com/rss/markets", # Mint Markets
    ]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(NewsService, cls).__new__(cls)
            cls._instance.analyzer = SentimentIntensityAnalyzer()
            cls._instance.news_cache = []
            cls._instance.last_fetch_time = 0
            cls._instance.cache_expiry = 300 # 5 Minutes
            cls._instance.market_sentiment_score = 0.0 # -1.0 to 1.0
            cls._instance._lock = threading.Lock()
        return cls._instance

    def _clean_text(self, text):
        """Remove HTML tags and extra spaces"""
        clean = re.sub('<[^<]+?>', '', text)
        return clean.strip()

    def fetch_news(self):
        """
        Fetches news from RSS feeds, deduplicates, and analyzes sentiment.
        Returns: Dict with 'articles' and 'overall_sentiment'
        """
        # Cache Check (Quick Read)
        if time.time() - self.last_fetch_time < self.cache_expiry and self.news_cache:
            return {
                "articles": self.news_cache,
                "sentiment": self.market_sentiment_score
            }
        
        # Acquire Lock - allows only one thread to update cache
        with self._lock:
            # Check again inside lock (double-checked locking)
            if time.time() - self.last_fetch_time < self.cache_expiry and self.news_cache:
                 return {
                    "articles": self.news_cache,
                    "sentiment": self.market_sentiment_score
                }

            logger.info("NewsService: Fetching latest market news...")
        articles = []
        total_score = 0
        count = 0
        
        seen_titles = set()

        for url in self.RSS_FEEDS:
            try:
                # Use requests with timeout to prevent hanging
                resp = requests.get(url, timeout=5)
                if resp.status_code != 200: continue
                
                feed = feedparser.parse(resp.content)
                for entry in feed.entries[:5]: # Top 5 per feed
                    title = self._clean_text(entry.title)
                    
                    if title in seen_titles: continue
                    seen_titles.add(title)
                    
                    # Sentiment Analysis
                    vs = self.analyzer.polarity_scores(title)
                    compound = vs['compound']
                    
                    # Store Article
                    article = {
                        "title": title,
                        "link": entry.link,
                        "source": feed.feed.get('title', 'Unknown'),
                        "published": entry.get('published', ''),
                        "sentiment": compound,
                        "sentiment_label": self._get_label(compound)
                    }
                    articles.append(article)
                    
                    total_score += compound
                    count += 1
            except Exception as e:
                logger.error(f"Error fetching RSS {url}: {e}")

        # Update State
        self.news_cache = articles
        if count > 0:
            self.market_sentiment_score = round(total_score / count, 2)
        else:
            self.market_sentiment_score = 0.0
            
        self.last_fetch_time = time.time()
        
        logger.info(f"NewsService: Fetched {len(articles)} articles. Global Sentiment: {self.market_sentiment_score}")
        
        return {
            "articles": self.news_cache,
            "sentiment": self.market_sentiment_score
        }
    
    def _get_label(self, score):
        if score >= 0.05: return "BULLISH"
        if score <= -0.05: return "BEARISH"
        return "NEUTRAL"

    def get_sentiment_score(self):
        self.fetch_news() # Ensure fresh
        return self.market_sentiment_score

news_service = NewsService()

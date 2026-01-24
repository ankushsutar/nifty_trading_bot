'use client';

import React, { useEffect, useState } from 'react';
import { Newspaper, TrendingUp, TrendingDown, Minus } from 'lucide-react';
import { motion } from 'framer-motion';
import Card from './ui/Card';

interface Article {
    title: string;
    link: string;
    source: string;
    published: string;
    sentiment: number;
    sentiment_label: string;
}

interface NewsData {
    articles: Article[];
    sentiment: number;
}

export default function NewsFeed() {
  const [data, setData] = useState<NewsData>({ articles: [], sentiment: 0 });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchNews = async () => {
        try {
            const res = await fetch('http://localhost:8000/api/news');
            const json = await res.json();
            setData(json);
            setLoading(false);
        } catch (e) {
            console.error(e);
        }
    };
    
    fetchNews();
    const interval = setInterval(fetchNews, 60000); // 1 min poll
    return () => clearInterval(interval);
  }, []);

  const getSentimentColor = (score: number) => {
      if (score > 0.05) return "text-green-400";
      if (score < -0.05) return "text-red-400";
      return "text-gray-400";
  };

  const getSentimentIcon = (score: number) => {
      if (score > 0.05) return <TrendingUp size={14} className="text-green-400" />;
      if (score < -0.05) return <TrendingDown size={14} className="text-red-400" />;
      return <Minus size={14} className="text-gray-400" />;
  };

  return (
    <Card title="Global Intel Stream" icon={<Newspaper size={18} />}>
        {loading ? (
             <div className="h-48 flex items-center justify-center text-xs text-gray-600">
                 DECRYPTING NEWS FEEDS...
             </div>
        ) : (
            <div className="flex flex-col h-full">
                {/* Sentiment Header */}
                <div className="flex items-center justify-between bg-white/5 p-2 rounded mb-3">
                     <span className="text-xs text-gray-400 uppercase">Market Sentiment</span>
                     <div className="flex items-center gap-2">
                         {getSentimentIcon(data.sentiment)}
                         <span className={`text-sm font-bold ${getSentimentColor(data.sentiment)}`}>
                             {(data.sentiment * 100).toFixed(0)}%
                         </span>
                     </div>
                </div>

                {/* Scrollable List */}
                <div className="flex-1 overflow-y-auto pr-2 space-y-3 custom-scrollbar" style={{ maxHeight: '300px' }}>
                    {data.articles.map((article, i) => (
                        <motion.div 
                            key={i}
                            initial={{ opacity: 0, x: -10 }}
                            animate={{ opacity: 1, x: 0 }}
                            transition={{ delay: i * 0.1 }}
                            className="p-2 border-b border-white/5 last:border-0 hover:bg-white/5 rounded transition-colors"
                        >
                            <div className="flex justify-between items-start gap-2">
                                <a href={article.link} target="_blank" rel="noopener noreferrer" className="text-xs font-medium text-gray-300 hover:text-cyan-400 leading-tight block">
                                    {article.title}
                                </a>
                                <div title={article.sentiment_label}>
                                    {getSentimentIcon(article.sentiment)}
                                </div>
                            </div>
                            <div className="flex justify-between items-center mt-1">
                                <span className="text-[10px] text-gray-600">{article.source}</span>
                                <span className={`text-[9px] px-1 rounded ${
                                    article.sentiment > 0.05 ? 'bg-green-500/10 text-green-500' : 
                                    article.sentiment < -0.05 ? 'bg-red-500/10 text-red-500' : 'bg-gray-500/10 text-gray-500'
                                }`}>
                                    {article.sentiment_label}
                                </span>
                            </div>
                        </motion.div>
                    ))}
                </div>
            </div>
        )}
    </Card>
  );
}

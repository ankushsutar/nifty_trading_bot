#!/usr/bin/env python3
"""
scripts/update_fii_dii.py — Update or Inspect FII/DII Derivatives Sentiment

Usage:
  # Check current sentiment
  python3 scripts/update_fii_dii.py --status

  # Set FII Long ratio directly (e.g. from Moneycontrol / NSE report)
  python3 scripts/update_fii_dii.py --ratio 32.5

  # Set exact contract counts
  python3 scripts/update_fii_dii.py --long 125000 --short 95000
"""

import sys
import os
import argparse

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.core.fii_dii_analyzer import FIIDIIAnalyzer


def main():
    parser = argparse.ArgumentParser(description="FII/DII Institutional Sentiment Manager")
    parser.add_argument("--status", action="store_true", help="Display current sentiment and ratios")
    parser.add_argument("--ratio", type=float, help="Set FII Index Futures Long Ratio (0 - 100)")
    parser.add_argument("--long", type=int, help="FII Index Futures Long Contracts")
    parser.add_argument("--short", type=int, help="FII Index Futures Short Contracts")
    parser.add_argument("--dii-long", type=int, default=0, help="DII Index Futures Long Contracts")
    parser.add_argument("--dii-short", type=int, default=0, help="DII Index Futures Short Contracts")

    args = parser.parse_args()
    analyzer = FIIDIIAnalyzer()

    if args.ratio is not None:
        if not (0.0 <= args.ratio <= 100.0):
            print("❌ Error: Ratio must be between 0.0 and 100.0")
            sys.exit(1)
        record = analyzer.update_from_ratio(args.ratio, source="CLI_RATIO")
        print(f"✅ Successfully updated FII Long Ratio to {args.ratio:.1f}%")
        print(f"   Regime: {record['regime']} | Bias: {record['bias']}")
        return

    if args.long is not None and args.short is not None:
        record = analyzer.update_from_values(
            fii_long=args.long,
            fii_short=args.short,
            dii_long=args.dii_long,
            dii_short=args.dii_short,
            source="CLI_CONTRACTS"
        )
        print(f"✅ Successfully updated FII/DII Contracts:")
        print(f"   FII Long: {args.long:,} | FII Short: {args.short:,} | Ratio: {record['fii_long_ratio']:.1f}%")
        print(f"   Regime: {record['regime']} | Bias: {record['bias']}")
        return

    # Default action: show status
    sentiment = analyzer.get_sentiment()
    print("==================================================")
    print("🏛️  FII / DII INSTITUTIONAL DERIVATIVES SENTIMENT")
    print("==================================================")
    print(f"📅 Date:         {sentiment.get('date')}")
    print(f"📊 FII Ratio:    {sentiment.get('fii_long_ratio', 0.0):.1f}% Long")
    print(f"📈 Regime:       {sentiment.get('regime')}")
    print(f"🎯 Market Bias:  {sentiment.get('bias')}")
    print(f"📦 FII Long:     {sentiment.get('fii_index_futures_long', 0):,} contracts")
    print(f"📦 FII Short:    {sentiment.get('fii_index_futures_short', 0):,} contracts")
    print(f"ℹ️  Source:       {sentiment.get('source')}")
    print(f"⏰ Last Updated: {sentiment.get('last_updated')}")
    print("==================================================")


if __name__ == "__main__":
    main()

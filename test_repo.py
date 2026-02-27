from bot.core.trade_repo import trade_repo
print("PAPER trades:", trade_repo.get_today_trades(mode="PAPER"))
print("LIVE trades:", trade_repo.get_today_trades(mode="LIVE"))

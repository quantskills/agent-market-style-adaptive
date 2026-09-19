"""Probe PandaData endpoints required to reproduce the reference strategy.

Credentials are entered interactively and are never stored.
"""
from __future__ import annotations

import getpass

import panda_data as pdx


def show(label, call):
    try:
        frame = call()
        print(f"{label}: rows={len(frame)} columns={list(frame.columns)}", flush=True)
        if not frame.empty:
            print(frame.head(2).to_string(index=False), flush=True)
    except Exception as exc:
        print(f"{label}: ERROR {type(exc).__name__}: {exc}", flush=True)


def main():
    user = input("PandaData user: ")
    password = getpass.getpass("PandaData password: ")
    pdx.init_token(user, password)
    del password
    show("csi1000_weights", lambda: pdx.get_index_weights(index_symbol="000852.SH", start_date="20240830", end_date="20240830"))
    show("csi300_weights", lambda: pdx.get_index_weights(index_symbol="000300.SH", start_date="20240830", end_date="20240830"))
    show("smallcap_index_weights", lambda: pdx.get_index_weights(index_symbol="399101.SZ", start_date="20240830", end_date="20240830"))
    show("industry_l2", lambda: pdx.get_stock_industry(stock_symbol="000001.SZ", level="L2"))
    show("fina_performance", lambda: pdx.get_fina_performance(end_quarter="2024q2"))
    show("fina_ex", lambda: pdx.get_fina_ex(start_quarter="2024q2", end_quarter="2024q2", is_latest=False))


if __name__ == "__main__":
    main()

"""立て替え精算の計算ロジック。

旅館の経費はオーナー（admin）の負担という前提のもと、
社員が個人の財布から立て替えた経費は、全額オーナーが本人に払い戻す。
オーナー自身が立て替えた分は払い戻し不要（自分の経費として扱う）。
"""


def calc_settlements(unsettled_transactions, active_users):
    """
    unsettled_transactions: models.unsettled_expenses() の結果（sqlite3.Row のリスト）
    active_users: models.list_users(active_only=True) の結果

    戻り値: (balances, all_transaction_ids)
      balances: [{'from_id', 'from_name', 'to_id', 'to_name', 'amount'}]（from = オーナー）
      all_transaction_ids: 今回の精算で処理対象になる取引IDの一覧
    """
    owner = next((u for u in active_users if u["role"] == "admin"), None)
    if owner is None:
        return [], []

    user_by_id = {u["id"]: u for u in active_users}
    totals = {}
    all_transaction_ids = []

    for t in unsettled_transactions:
        paid_by = t["paid_by"]
        if paid_by not in user_by_id:
            # 非アクティブ化されたメンバーが立て替えたものは精算対象外
            continue
        all_transaction_ids.append(t["id"])
        if paid_by == owner["id"]:
            continue
        totals[paid_by] = totals.get(paid_by, 0) + t["amount"]

    balances = [
        {
            "from_id": owner["id"],
            "from_name": owner["name"],
            "to_id": uid,
            "to_name": user_by_id[uid]["name"],
            "amount": round(amount),
        }
        for uid, amount in sorted(totals.items(), key=lambda kv: -kv[1])
    ]
    return balances, all_transaction_ids

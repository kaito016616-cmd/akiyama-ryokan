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


def calc_personal_balances(unsettled_items, active_users):
    """個人の割り勘（食費など）用。

    無関係な第三者に肩代わりさせる「全員分の最小送金」は行わず、
    実際に貸し借りが発生した「同じ2人同士」の債権債務だけを相殺する。
    例）安達が宮嶋に2000円分、宮嶋が安達に880円分の借りがあれば、
        両者の間の差額（安達→宮嶋 1120円）だけを算出する。

    unsettled_items: models.unsettled_personal_items() の結果
                      （各行に payer_id・user_id・amount・id を持つ）
    """
    user_by_id = {u["id"]: u for u in active_users}
    # debt[消費した人][立て替えた人] = 金額
    debt = {}
    all_item_ids = []

    for it in unsettled_items:
        payer_id, consumer_id = it["payer_id"], it["user_id"]
        if payer_id not in user_by_id or consumer_id not in user_by_id:
            continue
        all_item_ids.append(it["id"])
        if payer_id == consumer_id:
            continue  # 自分の分は貸し借りなし
        debt.setdefault(consumer_id, {})
        debt[consumer_id][payer_id] = debt[consumer_id].get(payer_id, 0) + it["amount"]

    result = []
    seen_pairs = set()
    for a, owed_by_a in debt.items():
        for b in owed_by_a:
            pair = tuple(sorted((a, b)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            a_owes_b = debt.get(a, {}).get(b, 0)
            b_owes_a = debt.get(b, {}).get(a, 0)
            net = a_owes_b - b_owes_a
            if net > 0.5:
                result.append(
                    {"from_id": a, "from_name": user_by_id[a]["name"],
                     "to_id": b, "to_name": user_by_id[b]["name"], "amount": round(net)}
                )
            elif net < -0.5:
                result.append(
                    {"from_id": b, "from_name": user_by_id[b]["name"],
                     "to_id": a, "to_name": user_by_id[a]["name"], "amount": round(-net)}
                )

    result.sort(key=lambda r: -r["amount"])
    return result, all_item_ids

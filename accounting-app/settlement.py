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
    """個人の割り勘（食費など）用。特定の1人が肩代わりするのではなく、
    立て替えた人と実際に消費した人との間で貸し借りが発生するため、
    最小回数の送金で解消できる組み合わせを算出する。

    unsettled_items: models.unsettled_personal_items() の結果
                      （各行に payer_id・user_id・amount・id を持つ）
    """
    user_by_id = {u["id"]: u for u in active_users}
    balance = {uid: 0.0 for uid in user_by_id}
    all_item_ids = []

    for it in unsettled_items:
        payer_id, consumer_id = it["payer_id"], it["user_id"]
        if payer_id not in balance or consumer_id not in balance:
            continue
        all_item_ids.append(it["id"])
        balance[payer_id] += it["amount"]
        balance[consumer_id] -= it["amount"]

    creditors = sorted(
        [[uid, bal] for uid, bal in balance.items() if bal > 0.5],
        key=lambda x: -x[1],
    )
    debtors = sorted(
        [[uid, bal] for uid, bal in balance.items() if bal < -0.5],
        key=lambda x: x[1],
    )

    result = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        d_id, d_bal = debtors[i]
        c_id, c_bal = creditors[j]
        pay = min(-d_bal, c_bal)
        result.append(
            {
                "from_id": d_id,
                "from_name": user_by_id[d_id]["name"],
                "to_id": c_id,
                "to_name": user_by_id[c_id]["name"],
                "amount": round(pay),
            }
        )
        debtors[i][1] += pay
        creditors[j][1] -= pay
        if abs(debtors[i][1]) < 0.5:
            i += 1
        if abs(creditors[j][1]) < 0.5:
            j += 1

    return result, all_item_ids

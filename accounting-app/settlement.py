"""立て替え精算の計算ロジック。

未精算の経費（誰かが個人の財布から立て替えたもの）を、
旅館の運営メンバー全員で均等負担する前提でならし、
最小回数の送金で解消できる組み合わせを算出する。
"""


def calc_settlements(unsettled_transactions, active_users):
    """
    unsettled_transactions: models.unsettled_expenses() の結果（sqlite3.Row のリスト）
    active_users: models.list_users(active_only=True) の結果

    戻り値: [{'from_id', 'from_name', 'to_id', 'to_name', 'amount', 'transaction_ids'}]
    """
    if not active_users:
        return [], []

    user_by_id = {u["id"]: u for u in active_users}
    n = len(active_users)

    # balance[uid] > 0 : 受け取るべき人（立て替えた分だけ他人の負担も肩代わりした）
    # balance[uid] < 0 : 支払うべき人
    balance = {u["id"]: 0.0 for u in active_users}
    tx_by_user = {u["id"]: [] for u in active_users}

    for t in unsettled_transactions:
        paid_by = t["paid_by"]
        if paid_by not in balance:
            # 精算対象メンバーが非アクティブ化されている場合はスキップ
            continue
        per_head = t["amount"] / n
        for uid in balance:
            balance[uid] -= per_head
        balance[paid_by] += t["amount"]
        tx_by_user[paid_by].append(t["id"])

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

    all_transaction_ids = [t["id"] for t in unsettled_transactions if t["paid_by"] in balance]
    return result, all_transaction_ids

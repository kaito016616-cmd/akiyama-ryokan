import os
from datetime import date
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for
from werkzeug.utils import secure_filename

import models
import settlement

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "akiyama-ryokan-accounting-dev-key")

RECEIPT_DIR = Path(__file__).parent / "static" / "receipts"
RECEIPT_DIR.mkdir(parents=True, exist_ok=True)

app.teardown_appcontext(models.close_db)


def current_year_month():
    return date.today().strftime("%Y-%m")


@app.route("/")
def dashboard():
    ym = request.args.get("ym", current_year_month())
    summary = models.month_summary(ym)

    active_users = models.list_users(active_only=True)
    unsettled = models.unsettled_expenses()
    balances, _ = settlement.calc_settlements(unsettled, active_users)

    return render_template(
        "dashboard.html",
        ym=ym,
        summary=summary,
        balances=balances,
        unsettled_count=len(unsettled),
        is_closed=models.is_closed(ym),
    )


@app.route("/transactions", methods=["GET", "POST"])
def transactions():
    if request.method == "POST":
        ym = request.form["date"][:7]
        if models.is_closed(ym):
            flash(f"{ym} は月次締め済みのため登録できません。", "error")
            return redirect(url_for("transactions"))

        receipt_path = None
        file = request.files.get("receipt")
        if file and file.filename:
            filename = secure_filename(file.filename)
            unique_name = f"{request.form['date']}_{filename}"
            file.save(RECEIPT_DIR / unique_name)
            receipt_path = f"receipts/{unique_name}"

        paid_by = request.form.get("paid_by") or None
        models.add_transaction(
            type_=request.form["type"],
            amount=int(request.form["amount"]),
            category_id=int(request.form["category_id"]),
            paid_by=int(paid_by) if paid_by else None,
            date=request.form["date"],
            memo=request.form.get("memo", ""),
            receipt_path=receipt_path,
        )
        flash("登録しました。", "success")
        return redirect(url_for("transactions"))

    return render_template(
        "transactions.html",
        transactions=models.list_transactions(),
        categories=models.list_categories(),
        users=models.list_users(active_only=True),
        today=date.today().isoformat(),
    )


@app.route("/settlements", methods=["GET", "POST"])
def settlements_view():
    active_users = models.list_users(active_only=True)
    unsettled = models.unsettled_expenses()
    balances, all_tx_ids = settlement.calc_settlements(unsettled, active_users)

    if request.method == "POST":
        if not balances:
            flash("精算対象の未精算取引がありません。", "error")
            return redirect(url_for("settlements_view"))
        for b in balances:
            models.create_settlement(b["from_id"], b["to_id"], b["amount"])
        settlement_id = models.list_settlements(limit=1)[0]["id"]
        models.mark_settled(all_tx_ids, settlement_id)
        flash("精算を実行しました。", "success")
        return redirect(url_for("settlements_view"))

    return render_template(
        "settlements.html",
        balances=balances,
        unsettled=unsettled,
        history=models.list_settlements(),
    )


@app.route("/reports")
def reports():
    ym = request.args.get("ym", current_year_month())
    expense_breakdown = models.category_breakdown(ym, "expense")
    income_breakdown = models.category_breakdown(ym, "income")
    trend = list(reversed(models.monthly_trend(6)))
    return render_template(
        "reports.html",
        ym=ym,
        expense_breakdown=expense_breakdown,
        income_breakdown=income_breakdown,
        trend=trend,
    )


@app.route("/settings", methods=["GET", "POST"])
def settings_view():
    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "user":
            models.add_user(request.form["name"], request.form.get("role", "member"))
            flash("メンバーを追加しました。", "success")
        elif form_type == "category":
            models.add_category(
                request.form["name"],
                request.form["type"],
                float(request.form["tax_rate"]),
            )
            flash("カテゴリを追加しました。", "success")
        elif form_type == "toggle_user":
            user_id = int(request.form["user_id"])
            active = request.form["active"] == "1"
            models.set_user_active(user_id, active)
        return redirect(url_for("settings_view"))

    return render_template(
        "settings.html",
        users=models.list_users(),
        categories=models.list_categories(),
    )


@app.route("/closings", methods=["GET", "POST"])
def closings_view():
    if request.method == "POST":
        ym = request.form["year_month"]
        closed_by = request.form.get("closed_by") or None
        if models.is_closed(ym):
            flash(f"{ym} はすでに締め済みです。", "error")
        else:
            models.close_month(ym, int(closed_by) if closed_by else None)
            flash(f"{ym} を月次締めしました。", "success")
        return redirect(url_for("closings_view"))

    return render_template(
        "closings.html",
        closings=models.list_closings(),
        users=models.list_users(active_only=True),
        current_ym=current_year_month(),
    )


if __name__ == "__main__":
    if not models.DB_PATH.exists():
        models.init_db()
    else:
        # 既存DBにも新しいカテゴリのデフォルト値を反映
        models.init_db()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5001, host="0.0.0.0")

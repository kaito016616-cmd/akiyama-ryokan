import os
from datetime import date
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
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


@app.before_request
def load_current_user():
    g.user = None
    if "user_id" in session:
        g.user = models.get_user(session["user_id"])
        if g.user is None or not g.user["active"]:
            session.clear()
            g.user = None

    if request.endpoint in ("login", "setup", "static"):
        return
    if not models.list_users():
        return redirect(url_for("setup"))
    if g.user is None:
        return redirect(url_for("login"))


@app.context_processor
def inject_user():
    return {"current_user": g.get("user")}


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None or g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.route("/setup", methods=["GET", "POST"])
def setup():
    if models.list_users():
        return redirect(url_for("login"))
    if request.method == "POST":
        pin = request.form["pin_code"]
        models.add_user(request.form["name"], role="admin", pin_code=pin)
        new_user = models.list_users()[0]
        session["user_id"] = new_user["id"]
        flash("管理者アカウントを作成しました。", "success")
        return redirect(url_for("dashboard"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user_id = int(request.form["user_id"])
        pin = request.form["pin_code"]
        user = models.find_user_by_pin(user_id, pin)
        if user is None:
            flash("名前またはPINが正しくありません。", "error")
            return redirect(url_for("login"))
        session["user_id"] = user["id"]
        return redirect(url_for("dashboard"))
    return render_template("login.html", users=models.list_users(active_only=True))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


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
        form_type = request.form.get("form_type", "business")

        if form_type == "personal":
            active_users = models.list_users(active_only=True)
            items = []
            for u in active_users:
                raw = request.form.get(f"item_{u['id']}", "").strip()
                if raw:
                    items.append((u["id"], int(raw)))
            if not items:
                flash("誰か1人以上の金額を入力してください。", "error")
                return redirect(url_for("transactions"))
            models.add_personal_expense(
                date=request.form["date"],
                payer_id=int(request.form["payer_id"]),
                memo=request.form.get("memo", ""),
                items=items,
                created_by=g.user["id"],
            )
            flash("個人の立て替えを登録しました。", "success")
            return redirect(url_for("transactions"))

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
            created_by=g.user["id"],
        )
        flash("登録しました。", "success")
        return redirect(url_for("transactions"))

    active_users = models.list_users(active_only=True)
    personal_unsettled = models.unsettled_personal_items()
    personal_balances, _ = settlement.calc_personal_balances(personal_unsettled, active_users)

    return render_template(
        "transactions.html",
        transactions=models.list_transactions(),
        categories=models.list_categories(),
        users=active_users,
        today=date.today().isoformat(),
        personal_expenses=models.list_personal_expenses(limit=30),
        personal_balances=personal_balances,
        personal_settlements=models.list_personal_settlements(limit=20),
    )


@app.route("/personal-settlements", methods=["POST"])
def execute_personal_settlement():
    active_users = models.list_users(active_only=True)
    unsettled_items = models.unsettled_personal_items()
    balances, item_ids = settlement.calc_personal_balances(unsettled_items, active_users)
    if not balances:
        flash("個人精算の対象がありません。", "error")
        return redirect(url_for("transactions"))
    for b in balances:
        models.create_personal_settlement(b["from_id"], b["to_id"], b["amount"])
    settlement_id = models.list_personal_settlements(limit=1)[0]["id"]
    models.mark_personal_settled(item_ids, settlement_id)
    flash("個人精算を実行しました。", "success")
    return redirect(url_for("transactions"))


def _can_edit(tx):
    """締め済みの月は誰も編集不可。精算済みの取引は他メンバーの精算履歴に影響するためadminのみ。
    それ以外は入力した本人 or admin のみ編集・削除可。"""
    if tx["locked"]:
        return False
    if g.user["role"] == "admin":
        return True
    if tx["is_settled"]:
        return False
    return tx["created_by"] == g.user["id"]


@app.route("/transactions/<int:transaction_id>/edit", methods=["GET", "POST"])
def edit_transaction(transaction_id):
    tx = models.get_transaction(transaction_id)
    if tx is None:
        abort(404)
    if not _can_edit(tx):
        abort(403)

    if request.method == "POST":
        ym = request.form["date"][:7]
        if models.is_closed(ym):
            flash(f"{ym} は月次締め済みのため変更できません。", "error")
            return redirect(url_for("transactions"))

        receipt_path = None
        file = request.files.get("receipt")
        if file and file.filename:
            filename = secure_filename(file.filename)
            unique_name = f"{request.form['date']}_{filename}"
            file.save(RECEIPT_DIR / unique_name)
            receipt_path = f"receipts/{unique_name}"

        paid_by = request.form.get("paid_by") or None
        models.update_transaction(
            transaction_id,
            type_=request.form["type"],
            amount=int(request.form["amount"]),
            category_id=int(request.form["category_id"]),
            paid_by=int(paid_by) if paid_by else None,
            date=request.form["date"],
            memo=request.form.get("memo", ""),
            receipt_path=receipt_path,
        )
        flash("取引を更新しました。", "success")
        return redirect(url_for("transactions"))

    return render_template(
        "transaction_edit.html",
        tx=tx,
        categories=models.list_categories(),
        users=models.list_users(active_only=True),
    )


@app.route("/transactions/<int:transaction_id>/delete", methods=["POST"])
def delete_transaction(transaction_id):
    tx = models.get_transaction(transaction_id)
    if tx is None:
        abort(404)
    if not _can_edit(tx):
        abort(403)
    models.delete_transaction(transaction_id)
    flash("取引を削除しました。", "success")
    return redirect(url_for("transactions"))


@app.route("/settlements", methods=["GET", "POST"])
def settlements_view():
    active_users = models.list_users(active_only=True)
    unsettled = models.unsettled_expenses()
    balances, all_tx_ids = settlement.calc_settlements(unsettled, active_users)

    if request.method == "POST":
        if g.user["role"] != "admin":
            abort(403)
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
@admin_required
def settings_view():
    if request.method == "POST":
        form_type = request.form.get("form_type")
        if form_type == "user":
            pin = request.form.get("pin_code") or "0000"
            models.add_user(request.form["name"], request.form.get("role", "member"), pin)
            flash("メンバーを追加しました。", "success")
        elif form_type == "edit_user":
            user_id = int(request.form["user_id"])
            models.update_user(user_id, request.form["name"], request.form["role"])
            pin = request.form.get("pin_code")
            if pin:
                models.set_user_pin(user_id, pin)
            flash("メンバー情報を更新しました。", "success")
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
@admin_required
def closings_view():
    if request.method == "POST":
        ym = request.form["year_month"]
        if models.is_closed(ym):
            flash(f"{ym} はすでに締め済みです。", "error")
        else:
            models.close_month(ym, g.user["id"])
            flash(f"{ym} を月次締めしました。", "success")
        return redirect(url_for("closings_view"))

    return render_template(
        "closings.html",
        closings=models.list_closings(),
        current_ym=current_year_month(),
    )


if __name__ == "__main__":
    if not models.DB_PATH.exists():
        models.init_db()
    else:
        # 既存DBにも新しいカテゴリのデフォルト値を反映
        models.init_db()
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=5001, host="0.0.0.0")

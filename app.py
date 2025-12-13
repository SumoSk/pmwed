import os
from functools import wraps
from flask import Flask, render_template, jsonify, request, Response, session
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "change-me-now")

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("❌ Missing SUPABASE_URL or SUPABASE_KEY in .env")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "1234")


# ---------------------------
# Admin guard (API-friendly)
# ---------------------------
def admin_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not session.get("is_admin"):
            return jsonify({"success": False, "message": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapper


# ---------------------------
# Pages
# ---------------------------
@app.route("/")
def booking_page():
    return render_template("booking.html")


# ---------------------------
# API: admin login/logout
# ---------------------------
@app.route("/api/admin/login", methods=["POST"])
def api_admin_login():
    data = request.json or {}
    pw = str(data.get("password", "")).strip()
    if pw == ADMIN_PASSWORD:
        session["is_admin"] = True
        return jsonify({"success": True})
    return jsonify({"success": False, "message": "รหัสไม่ถูกต้อง"}), 401


@app.route("/api/admin/logout", methods=["POST"])
@admin_required
def api_admin_logout():
    session.pop("is_admin", None)
    return jsonify({"success": True})


# ---------------------------
# API: tables summary
# returns: [{table_number, table_name, seats:[{id, seat_number, status, guest_id, guest_name, guest_intro, guest_gift_amount, arrived, reserved_for}]}]
# ---------------------------
@app.route("/api/tables", methods=["GET"])
def api_tables():
    try:
        resp = (
            supabase
            .table("tables")
            .select(
                "table_number, table_name, "
                "seats("
                "  id, seat_number, status, arrived, reserved_for, guest_id, "
                "  guests(id, name, intro, gift_amount)"
                ")"
            )
            .order("table_number")
            .order("seat_number", foreign_table="seats")
            .execute()
        )

        tables = resp.data or []
        result = []

        for t in tables:
            seats_out = []
            seats = t.get("seats") or []

            for s in seats:
                g = s.get("guests") or {}

                seats_out.append({
                    "id": s.get("id"),
                    "seat_number": s.get("seat_number"),
                    "status": s.get("status") or "available",
                    "arrived": bool(s.get("arrived") or False),

                    # กันที่นั่ง/จองไกด์
                    "reserved_for": s.get("reserved_for") or "",

                    # ข้อมูลแขก (ถ้ามี)
                    "guest_id": s.get("guest_id") or g.get("id"),
                    "guest_name": g.get("name") or "",
                    "guest_intro": g.get("intro") or "",
                    "guest_gift_amount": g.get("gift_amount"),
                })

            result.append({
                "table_number": t.get("table_number"),
                "table_name": t.get("table_name") or "",
                "seats": seats_out
            })

        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# API: book seat (RPC)
# expects: {seatId, name, intro}
# ---------------------------
@app.route("/api/book", methods=["POST"])
def api_book():
    try:
        data = request.json or {}
        seat_id = data.get("seatId")
        name = (data.get("name") or "").strip()
        intro = (data.get("intro") or "").strip()

        if not seat_id or not name or not intro:
            return jsonify({"success": False, "message": "ข้อมูลไม่ครบ"}), 400

        rpc = supabase.rpc("book_seat", {
            "p_seat_id": seat_id,
            "p_guest_name": name,
            "p_intro": intro
        }).execute()

        result = rpc.data
        if not result or result.get("success") is not True:
            return jsonify({"success": False, "message": (result or {}).get("message", "จองไม่สำเร็จ")}), 400

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------
# API: confirm arrival (RPC)
# expects: {seatId}
# ---------------------------
@app.route("/api/arrive", methods=["POST"])
def api_arrive():
    try:
        data = request.json or {}
        seat_id = data.get("seatId")
        if not seat_id:
            return jsonify({"success": False, "message": "missing seatId"}), 400

        rpc = supabase.rpc("confirm_arrival", {"p_seat_id": seat_id}).execute()
        result = rpc.data
        if not result or result.get("success") is not True:
            return jsonify({"success": False, "message": (result or {}).get("message", "ยืนยันไม่สำเร็จ")}), 400

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------
# API: wishes submit
# expects: {name, message}
# ---------------------------
@app.route("/api/wish", methods=["POST"])
def api_wish():
    try:
        data = request.json or {}
        name = (data.get("name") or "").strip()
        message = (data.get("message") or "").strip()

        if not name or not message:
            return jsonify({"success": False, "message": "กรอกชื่อและคำอวยพรให้ครบ"}), 400

        supabase.table("wishes").insert({
            "name": name,
            "message": message
        }).execute()

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------
# ✅ API: wishes list (หน้าเว็บใช้โหลดโชว์)
# GET /api/wishes  -> {success, wishes:[...]}
# ---------------------------
@app.route("/api/wishes", methods=["GET"])
def api_wishes():
    try:
        # พยายาม order ด้วย created_at ก่อน (ถ้าตารางมี)
        try:
            resp = (
                supabase
                .table("wishes")
                .select("id,name,message,created_at")
                .order("created_at", desc=True)
                .limit(50)
                .execute()
            )
            return jsonify({"success": True, "wishes": resp.data or []})
        except Exception:
            # fallback ถ้าไม่มี created_at
            resp = (
                supabase
                .table("wishes")
                .select("id,name,message")
                .order("id", desc=True)
                .limit(50)
                .execute()
            )
            return jsonify({"success": True, "wishes": resp.data or []})

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------
# ADMIN: set table name
# ---------------------------
@app.route("/api/admin/set_table_name", methods=["POST"])
@admin_required
def api_admin_set_table_name():
    try:
        data = request.json or {}
        table_number = int(data.get("table_number"))
        table_name = (data.get("table_name") or "").strip()

        supabase.table("tables").update({
            "table_name": table_name
        }).eq("table_number", table_number).execute()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------
# ADMIN: update guest
# ---------------------------
@app.route("/api/admin/guest/update", methods=["POST"])
@admin_required
def api_admin_update_guest():
    try:
        data = request.json or {}
        guest_id = data.get("guest_id")
        if not guest_id:
            return jsonify({"success": False, "message": "missing guest_id"}), 400

        name = (data.get("name") or "").strip()
        intro = (data.get("intro") or "").strip()
        gift_amount = data.get("gift_amount")

        # แปลง gift_amount เป็นเลขหรือ None
        try:
            gift_amount = float(gift_amount) if gift_amount not in (None, "",) else None
        except Exception:
            gift_amount = None

        supabase.table("guests").update({
            "name": name if name else None,
            "intro": intro if intro else None,
            "gift_amount": gift_amount
        }).eq("id", guest_id).execute()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------
# ADMIN: reserve seat tag (กันที่นั่ง)
# expects: {seat_id, reserved_for}
# ---------------------------
@app.route("/api/admin/seat/reserve", methods=["POST"])
@admin_required
def api_admin_seat_reserve():
    try:
        data = request.json or {}
        seat_id = data.get("seat_id")
        reserved_for = (data.get("reserved_for") or "").strip()

        if not seat_id:
            return jsonify({"success": False, "message": "missing seat_id"}), 400

        seat = (
            supabase
            .table("seats")
            .select("id,status")
            .eq("id", seat_id)
            .single()
            .execute()
        )
        seat_data = seat.data or {}

        if seat_data.get("status") == "booked":
            return jsonify({"success": False, "message": "ที่นั่งนี้ถูกจองแล้ว ไม่สามารถตั้งจองไกด์ได้"}), 400

        supabase.table("seats").update({
            "reserved_for": reserved_for if reserved_for else None
        }).eq("id", seat_id).execute()

        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------
# ADMIN: stats
# ---------------------------
@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def api_admin_stats():
    try:
        booked = (
            supabase.table("seats")
            .select("id", count="exact")
            .eq("status", "booked")
            .execute()
        )

        arrived = (
            supabase.table("seats")
            .select("id", count="exact")
            .eq("status", "booked")
            .eq("arrived", True)
            .execute()
        )

        wishes = (
            supabase.table("wishes")
            .select("id", count="exact")
            .execute()
        )

        booked_count = booked.count or 0
        arrived_count = arrived.count or 0
        wish_count = wishes.count or 0

        capacity = 320

        return jsonify({
            "success": True,
            "booked_count": booked_count,
            "arrived_count": arrived_count,
            "wish_count": wish_count,
            "capacity": capacity,
            "available_count": max(0, capacity - booked_count)
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------
# ADMIN: export CSV
# ---------------------------
@app.route("/admin/export.csv")
@admin_required
def export_csv():
    """
    CSV สำหรับแอดมิน: รวมชื่อโต๊ะ/ที่นั่ง/สถานะ/มาถึง/ชื่อ/แนะนำตัว/เงินช่วยเหลือ
    """
    try:
        resp = (
            supabase
            .table("tables")
            .select(
                "table_number, table_name, "
                "seats(seat_number, status, arrived, guest_id, guests(name, intro, gift_amount))"
            )
            .order("table_number")
            .execute()
        )

        rows = ["table_number,table_name,seat_number,status,arrived,guest_name,guest_intro,guest_gift_amount"]
        for t in resp.data or []:
            tn = t.get("table_number")
            tname = (t.get("table_name") or "").replace('"', '""')
            seats = sorted(t.get("seats") or [], key=lambda x: x.get("seat_number") or 0)

            for s in seats:
                g = s.get("guests") or {}
                name = (g.get("name") or "").replace('"', '""')
                intro = (g.get("intro") or "").replace('"', '""')
                gift = g.get("gift_amount", "")
                arrived = "true" if (s.get("arrived") is True) else "false"
                rows.append(
                    f'{tn},"{tname}",{s.get("seat_number")},{s.get("status")},{arrived},"{name}","{intro}","{gift}"'
                )

        csv_text = "\n".join(rows)
        return Response(
            csv_text,
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=guest_list.csv"}
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------
# Run (เหลือครั้งเดียวท้ายไฟล์)
# ---------------------------
if __name__ == "__main__":
    app.run(debug=True)

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from datetime import datetime

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///inventory.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = "change-this-later"

db = SQLAlchemy(app)


class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    barcode = db.Column(db.String(120), unique=True, nullable=False)
    quantity = db.Column(db.Integer, default=0)
    low_stock_threshold = db.Column(db.Integer, default=5)
    price = db.Column(db.Float, default=0.0)


class StockMovement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    change = db.Column(db.Integer, nullable=False)
    note = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship("Product", backref="movements")


class Employee(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    employee_code = db.Column(db.String(20), unique=True, nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_active = db.Column(db.Boolean, default=True)


def current_employee():
    employee_id = session.get("employee_id")
    if not employee_id:
        return None
    return Employee.query.get(employee_id)


def login_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        if "employee_id" not in session:
            flash("Please log in with your employee ID.", "warning")
            return redirect(url_for("login"))
        return route_function(*args, **kwargs)
    return wrapper


def admin_required(route_function):
    @wraps(route_function)
    def wrapper(*args, **kwargs):
        employee = current_employee()
        if not employee:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        if not employee.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("employee_dashboard"))
        return route_function(*args, **kwargs)
    return wrapper


@app.context_processor
def inject_employee():
    return {"logged_in_employee": current_employee()}


@app.route("/")
def dashboard():
    products = Product.query.order_by(Product.name.asc()).all()
    low_stock_products = Product.query.filter(Product.quantity <= Product.low_stock_threshold).order_by(Product.quantity.asc()).all()
    recent_movements = StockMovement.query.order_by(StockMovement.timestamp.desc()).limit(10).all()
    return render_template(
        "dashboard.html",
        products=products,
        low_stock_products=low_stock_products,
        recent_movements=recent_movements,
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        code = request.form.get("employee_code", "").strip()
        employee = Employee.query.filter_by(employee_code=code, is_active=True).first()

        if employee:
            session["employee_id"] = employee.id
            session["employee_name"] = employee.name
            session["is_admin"] = employee.is_admin
            flash(f"Welcome, {employee.name}!", "success")
            if employee.is_admin:
                return redirect(url_for("admin_dashboard"))
            return redirect(url_for("employee_dashboard"))

        flash("Invalid employee ID.", "danger")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for("dashboard"))


@app.route("/employee")
@login_required
def employee_dashboard():
    low_stock_products = Product.query.filter(
        Product.quantity <= Product.low_stock_threshold
    ).order_by(Product.quantity.asc(), Product.name.asc()).all()
    recent_movements = StockMovement.query.order_by(StockMovement.timestamp.desc()).limit(8).all()
    return render_template(
        "employee.html",
        low_stock_count=len(low_stock_products),
        low_stock_products=low_stock_products,
        recent_movements=recent_movements,
    )


@app.route("/scanner", methods=["GET", "POST"])
@login_required
def scanner():
    message = None
    product = None

    if request.method == "POST":
        barcode = request.form.get("barcode", "").strip()
        quantity = request.form.get("quantity", "1").strip()
        action = request.form.get("action", "add").strip()

        try:
            quantity = int(quantity)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            flash("Quantity must be a positive whole number.", "danger")
            return redirect(url_for("scanner"))

        product = Product.query.filter_by(barcode=barcode).first()
        if not product:
            flash("Product not found for that barcode.", "danger")
            return redirect(url_for("scanner"))

        change_amount = quantity if action == "add" else -quantity
        product.quantity += change_amount

        db.session.add(StockMovement(product_id=product.id, change=change_amount, note=f"Scanner: {action}"))
        db.session.commit()

        message = f"{product.name} updated successfully."

    return render_template("scanner.html", message=message, product=product)


def get_checkout_cart():
    return session.get("checkout_cart", [])


def save_checkout_cart(cart):
    session["checkout_cart"] = cart
    session.modified = True


def get_checkout_selected_index():
    selected_index = session.get("checkout_selected_index")
    if isinstance(selected_index, int):
        return selected_index
    return None


def set_checkout_selected_index(selected_index):
    if selected_index is None:
        session.pop("checkout_selected_index", None)
    else:
        session["checkout_selected_index"] = selected_index
    session.modified = True


@app.route("/checkout", methods=["GET", "POST"])
@login_required
def checkout():
    cart = get_checkout_cart()
    selected_index = get_checkout_selected_index()
    last_action_item = None

    if selected_index is not None and (selected_index < 0 or selected_index >= len(cart)):
        selected_index = None
        set_checkout_selected_index(None)

    if request.method == "POST":
        action = request.form.get("action", "scan_add").strip() or "scan_add"
        shared_input = request.form.get("barcode", "").strip()
        quantity_raw = request.form.get("quantity", "1").strip()

        try:
            quantity = int(quantity_raw)
            if quantity <= 0:
                raise ValueError
        except ValueError:
            flash("Quantity must be a positive whole number.", "danger")
            return redirect(url_for("checkout"))

        if action == "scan_add":
            barcode = shared_input
            if not barcode:
                flash("Enter or scan a barcode first.", "warning")
                return redirect(url_for("checkout"))

            product = Product.query.filter_by(barcode=barcode).first()
            if not product:
                flash("Product not found for that barcode.", "danger")
                return redirect(url_for("checkout"))

            if product.quantity < quantity:
                flash(f"Not enough stock for {product.name}. Available: {product.quantity}", "danger")
                return redirect(url_for("checkout"))

            product.quantity -= quantity
            db.session.add(StockMovement(product_id=product.id, change=-quantity, note="Checkout sale"))

            existing_index = next((index for index, item in enumerate(cart) if item["product_id"] == product.id), None)
            if existing_index is None:
                cart.append({
                    "product_id": product.id,
                    "name": product.name,
                    "barcode": product.barcode,
                    "price": product.price,
                    "quantity": quantity,
                    "remaining_stock": product.quantity,
                    "low_stock_threshold": product.low_stock_threshold,
                })
                selected_index = len(cart) - 1
            else:
                cart[existing_index]["quantity"] += quantity
                cart[existing_index]["remaining_stock"] = product.quantity
                cart[existing_index]["price"] = product.price
                cart[existing_index]["low_stock_threshold"] = product.low_stock_threshold
                selected_index = existing_index

            db.session.commit()
            save_checkout_cart(cart)
            set_checkout_selected_index(selected_index)
            last_action_item = cart[selected_index]
            flash(f"Added {quantity} of {product.name} to checkout cart.", "success")
            return redirect(url_for("checkout"))

        if action == "select_item_by_number":
            if not shared_input:
                flash("Enter an item number first.", "warning")
                return redirect(url_for("checkout"))

            try:
                requested_slot = int(shared_input)
            except ValueError:
                flash("Item number must be a whole number.", "danger")
                return redirect(url_for("checkout"))

            selected_index = requested_slot - 1
            if selected_index < 0 or selected_index >= len(cart):
                flash("That cart item number does not exist.", "danger")
                return redirect(url_for("checkout"))

            set_checkout_selected_index(selected_index)
            flash(f"Selected item {requested_slot}: {cart[selected_index]['name']}", "info")
            return redirect(url_for("checkout"))

        if action == "update_qty":
            if selected_index is None or selected_index >= len(cart):
                flash("Select a cart item before updating quantity.", "warning")
                return redirect(url_for("checkout"))

            if not shared_input:
                flash("Enter the new quantity in the receive box first.", "warning")
                return redirect(url_for("checkout"))

            try:
                new_quantity = int(shared_input)
                if new_quantity <= 0:
                    raise ValueError
            except ValueError:
                flash("Updated quantity must be a positive whole number.", "danger")
                return redirect(url_for("checkout"))

            cart_item = cart[selected_index]
            product = Product.query.get(cart_item["product_id"])
            if not product:
                flash("That product no longer exists in inventory.", "danger")
                return redirect(url_for("checkout"))

            quantity_difference = new_quantity - cart_item["quantity"]
            if quantity_difference > 0:
                if product.quantity < quantity_difference:
                    flash(f"Not enough stock for {product.name}. Available: {product.quantity}", "danger")
                    return redirect(url_for("checkout"))
                product.quantity -= quantity_difference
                db.session.add(StockMovement(product_id=product.id, change=-quantity_difference, note="Checkout quantity update"))
            elif quantity_difference < 0:
                product.quantity += abs(quantity_difference)
                db.session.add(StockMovement(product_id=product.id, change=abs(quantity_difference), note="Checkout quantity reduction"))

            cart_item["quantity"] = new_quantity
            cart_item["remaining_stock"] = product.quantity
            cart_item["price"] = product.price
            cart_item["low_stock_threshold"] = product.low_stock_threshold

            db.session.commit()
            save_checkout_cart(cart)
            set_checkout_selected_index(selected_index)
            flash(f"Updated {product.name} to quantity {new_quantity}.", "success")
            return redirect(url_for("checkout"))

        if action == "delete_item":
            if selected_index is None or selected_index >= len(cart):
                flash("Select a cart item before deleting it.", "warning")
                return redirect(url_for("checkout"))

            cart_item = cart.pop(selected_index)
            product = Product.query.get(cart_item["product_id"])
            if product:
                product.quantity += cart_item["quantity"]
                db.session.add(StockMovement(product_id=product.id, change=cart_item["quantity"], note="Checkout item deleted"))
                db.session.commit()

            if not cart:
                selected_index = None
            elif selected_index >= len(cart):
                selected_index = len(cart) - 1

            save_checkout_cart(cart)
            set_checkout_selected_index(selected_index)
            flash(f"Removed {cart_item['name']} from the checkout cart.", "info")
            return redirect(url_for("checkout"))

        flash("That checkout action is not supported yet.", "warning")
        return redirect(url_for("checkout"))

    if selected_index is not None and 0 <= selected_index < len(cart):
        last_action_item = cart[selected_index]

    slot_count = max(8, len(cart))
    cart_line_count = len(cart)
    cart_unit_total = sum(item.get("quantity", 0) for item in cart)
    cart_total_amount = sum((item.get("price", 0) or 0) * item.get("quantity", 0) for item in cart)
    return render_template(
        "checkout.html",
        cart_items=cart,
        selected_index=selected_index,
        checked_out_product=last_action_item,
        slot_count=slot_count,
        cart_line_count=cart_line_count,
        cart_unit_total=cart_unit_total,
        cart_total_amount=cart_total_amount,
    )


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_dashboard():
    if request.method == "POST":
        form_type = request.form.get("form_type")

        if form_type == "add_employee":
            name = request.form.get("name", "").strip()
            employee_code = request.form.get("employee_code", "").strip()
            is_admin = request.form.get("is_admin") == "on"

            if not name or not employee_code:
                flash("Employee name and code are required.", "danger")
                return redirect(url_for("admin_dashboard"))

            existing = Employee.query.filter_by(employee_code=employee_code).first()
            if existing:
                flash("That employee code already exists.", "danger")
                return redirect(url_for("admin_dashboard"))

            employee = Employee(name=name, employee_code=employee_code, is_admin=is_admin, is_active=True)
            db.session.add(employee)
            db.session.commit()
            flash("Employee added successfully.", "success")
            return redirect(url_for("admin_dashboard"))

        if form_type == "toggle_employee":
            employee_id = request.form.get("employee_id", type=int)
            employee = Employee.query.get_or_404(employee_id)

            if employee.employee_code == "8478":
                flash("The primary admin account cannot be deactivated here.", "warning")
                return redirect(url_for("admin_dashboard"))

            employee.is_active = not employee.is_active
            db.session.commit()
            status_text = "activated" if employee.is_active else "deactivated"
            flash(f"{employee.name} was {status_text}.", "success")
            return redirect(url_for("admin_dashboard"))

        if form_type == "add_product":
            name = request.form.get("name", "").strip()
            barcode = request.form.get("barcode", "").strip()
            quantity = request.form.get("quantity", "0").strip()
            low_stock_threshold = request.form.get("low_stock_threshold", "5").strip()
            price = request.form.get("price", "0").strip()

            try:
                quantity = int(quantity)
                low_stock_threshold = int(low_stock_threshold)
                price = float(price)
            except ValueError:
                flash("Product values are invalid.", "danger")
                return redirect(url_for("admin_dashboard"))

            if not name or not barcode:
                flash("Product name and barcode are required.", "danger")
                return redirect(url_for("admin_dashboard"))

            existing = Product.query.filter_by(barcode=barcode).first()
            if existing:
                flash("That barcode already exists.", "danger")
                return redirect(url_for("admin_dashboard"))

            product = Product(
                name=name,
                barcode=barcode,
                quantity=quantity,
                low_stock_threshold=low_stock_threshold,
                price=price,
            )
            db.session.add(product)
            db.session.flush()
            db.session.add(StockMovement(product_id=product.id, change=quantity, note="Initial product setup"))
            db.session.commit()
            flash("Product added successfully.", "success")
            return redirect(url_for("admin_dashboard"))

        if form_type == "edit_product":
            product_id = request.form.get("product_id", type=int)
            product = Product.query.get_or_404(product_id)

            name = request.form.get("name", "").strip()
            barcode = request.form.get("barcode", "").strip()
            quantity = request.form.get("quantity", "0").strip()
            low_stock_threshold = request.form.get("low_stock_threshold", "5").strip()
            price = request.form.get("price", "0").strip()

            try:
                new_quantity = int(quantity)
                product.low_stock_threshold = int(low_stock_threshold)
                product.price = float(price)
            except ValueError:
                flash("Product values are invalid.", "danger")
                return redirect(url_for("admin_dashboard"))

            if not name or not barcode:
                flash("Product name and barcode are required.", "danger")
                return redirect(url_for("admin_dashboard"))

            existing_barcode = Product.query.filter(Product.barcode == barcode, Product.id != product.id).first()
            if existing_barcode:
                flash("That barcode is already being used by another product.", "danger")
                return redirect(url_for("admin_dashboard"))

            quantity_change = new_quantity - product.quantity
            product.name = name
            product.barcode = barcode
            product.quantity = new_quantity

            if quantity_change != 0:
                db.session.add(StockMovement(product_id=product.id, change=quantity_change, note="Admin product edit"))

            db.session.commit()
            flash(f"{product.name} updated successfully.", "success")
            return redirect(url_for("admin_dashboard"))

    employees = Employee.query.order_by(Employee.name.asc()).all()
    products = Product.query.order_by(Product.name.asc()).all()
    recent_movements = StockMovement.query.order_by(StockMovement.timestamp.desc()).limit(20).all()

    return render_template("admin.html", employees=employees, products=products, recent_movements=recent_movements)


@app.route("/setup")
def setup():
    db.create_all()

    admin = Employee.query.filter_by(employee_code="8478").first()
    if not admin:
        admin = Employee(name="Macoy Mino", employee_code="8478", is_admin=True, is_active=True)
        db.session.add(admin)
    elif admin.name != "Macoy Mino":
        admin.name = "Macoy Mino"

    sample_products = [
        {"name": "Pancake Mix", "barcode": "111111", "quantity": 3, "low_stock_threshold": 5, "price": 4.99},
        {"name": "Syrup", "barcode": "222222", "quantity": 10, "low_stock_threshold": 4, "price": 3.49},
        {"name": "Paper Towels", "barcode": "333333", "quantity": 20, "low_stock_threshold": 6, "price": 2.99},
    ]

    for item in sample_products:
        existing = Product.query.filter_by(barcode=item["barcode"]).first()
        if not existing:
            product = Product(**item)
            db.session.add(product)
            db.session.flush()
            db.session.add(StockMovement(product_id=product.id, change=product.quantity, note="Seed setup"))

    db.session.commit()
    return "Setup complete."


if __name__ == "__main__":
    app.run(debug=True, port=8000)

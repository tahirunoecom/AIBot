import json
from typing import Any, Text, Dict, List

import openai
from openai import OpenAI
import os
import re
import requests
import importlib.metadata
import stripe
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, EventType
from rasa_sdk.forms import FormValidationAction


API_BASE = "https://stageshipperapi.thedelivio.com/api"

class ActionShowCategoriesWithProducts(Action):
    def name(self) -> Text:
        return "action_show_categories_with_products"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        try:
            response = requests.post(f"{API_BASE}/getCategories", json={"zipcode": ""}, timeout=10)
            response.raise_for_status()
            data = response.json()
            categories = data.get("data", {}).get("getCategories", [])
            if not categories:
                dispatcher.utter_message(text="Sorry, no categories found.")
                return []
            all_products = []
            messages = []
            global_idx = 1  # unique index over all products
            for category in categories:
                cat_name = category.get("name", "Unnamed Category")
                products = category.get("getMasterProductOfCategory", [])
                if products:
                    prod_lines = []
                    for p in products[:5]:  # only first 5 products per category
                        title = p.get("title", "Unnamed Product")
                        try:
                            price = float(p.get("discounted_price", 0) or p.get("product_price", 0))
                            price_str = f"${price:.2f}"
                        except Exception:
                            price_str = "-"
                        discount = p.get("discount")
                        try:
                            qty = float(p.get("quantity", 0))
                            ordered = float(p.get("ordered_qty", 0))
                            available = int(qty - ordered)
                        except Exception:
                            available = None
                        extras = []
                        if discount and float(discount) > 0:
                            extras.append(f"{discount}% off")
                        if available is not None:
                            extras.append(f"{available} available")
                        extra_info = f" [{' | '.join(extras)}]" if extras else ""
                        prod_lines.append(f"{global_idx}. {title} ({price_str}){extra_info}")
                        all_products.append(p)
                        global_idx += 1
                    prod_text = "\n".join(prod_lines)
                else:
                    prod_text = "No products available"
                messages.append(f"Category: {cat_name}\n{prod_text}")
            full_message = "\n\n".join(messages)
            dispatcher.utter_message(text=full_message + "\n\nReply with the product number or name to select it.")

            return [SlotSet("recent_products", json.dumps(all_products))]
        except Exception as e:
            print(f"[EXCEPTION] in ActionShowCategoriesWithProducts: {e}")
            dispatcher.utter_message(text="Sorry, I couldn't fetch categories right now.")
            return []


class ActionSearchProducts(Action):
    def name(self) -> Text:
        return "action_search_products"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict]:

        # Get entities from the latest user message
        category = next(tracker.get_latest_entity_values("product_category"), None)
        name = next(tracker.get_latest_entity_values("product_name"), None)

        # Prioritize product_name over product_category
        if name:
            search_string = name
        elif category:
            search_string = category
        else:
            search_string = ""

        if not search_string:
            dispatcher.utter_message(text="Please specify a product name or category to search for.")
            return []

        url = f"{API_BASE}/getMasterProducts"
        json_body = {
            "wh_account_id": "",
            "upc": "",
            "ai_category_id": "",
            "ai_product_id": "",
            "product_id": "",
            "search_string": search_string,
            "zipcode": "",
            "user_id": "",
            "page": "1",
            "items": "10"
        }

        try:
            response = requests.post(url, json=json_body, timeout=8)
            data = response.json()

            products = data.get("data", [])
            if not isinstance(products, list) or not products:
                dispatcher.utter_message(text=f"No products found matching '{search_string}'.")
                return []

            product_lines = []
            for idx, p in enumerate(products[:5], start=1):
                title = p.get("title") or p.get("product_name") or "Unnamed Product"
                try:
                    price = float(p.get("discounted_price", 0) or p.get("product_price", 0))
                    price_str = f"${price:.2f}"
                except Exception:
                    price_str = "-"
                discount = p.get("discount")
                try:
                    qty = float(p.get("quantity", 0))
                    ordered = float(p.get("ordered_qty", 0))
                    available_qty = int(qty - ordered)
                except Exception:
                    available_qty = None
                extras = []
                if discount and float(discount) > 0:
                    extras.append(f"{discount}% off")
                if available_qty is not None:
                    extras.append(f"{available_qty} available")
                extra_info = f" [{' | '.join(extras)}]" if extras else ""
                product_lines.append(f"{idx}. {title} ({price_str}){extra_info}")

            message = "Here are some products:\n" + "\n".join(product_lines)
            dispatcher.utter_message(text=message + "\n\nReply with the product number or name to select it.")

            # Save product list in slot for selection later
            return [SlotSet("recent_products", json.dumps(products))]

        except Exception as e:
            print(f"[EXCEPTION] in ActionSearchProducts: {e}")
            dispatcher.utter_message(text="Sorry, I couldn't fetch products right now.")
            return []


class ActionSelectProduct(Action):
    def name(self) -> Text:
        return "action_select_product"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        user_text = tracker.latest_message.get("text", "").strip().lower()
        recent_products_json = tracker.get_slot("recent_products")
        if not recent_products_json:
            dispatcher.utter_message(text="I don't have any recently shown products to select from. Please search for products first.")
            return []
        try:
            products = json.loads(recent_products_json)
            if not isinstance(products, list):
                raise ValueError("recent_products slot did not contain a list!")
        except Exception as e:
            print(f"[EXCEPTION] loading recent products: {e}, slot content: {recent_products_json}")
            dispatcher.utter_message(text="Sorry, there was an internal error with product selection. Please show the categories/products again first.")
            return []
        selected_product = None
        if user_text.isdigit():
            index = int(user_text) - 1
            if 0 <= index < len(products):
                selected_product = products[index]
        if not selected_product:
            for p in products:
                title = p.get("title", "").lower()
                if user_text in title:
                    selected_product = p
                    break
        if not selected_product:
            dispatcher.utter_message(text="Sorry, I couldn't find a product matching your selection. Please try again.")
            return []
        title = selected_product.get("title", "Unnamed Product")
        try:
            price = float(selected_product.get("discounted_price")) if selected_product.get("discounted_price") else float(selected_product.get("product_price", 0))
            price_str = f"${price:.2f}"
        except Exception:
            price_str = "-"
        description = selected_product.get("description", "No description available.")
        product_type = selected_product.get("product_type", "Unknown")
        store_name = selected_product.get("store_name", "Unknown Store")
        discount = selected_product.get("discount")
        try:
            qty = float(selected_product.get("quantity", 0))
            ordered = float(selected_product.get("ordered_qty", 0))
            available = int(qty - ordered)
        except Exception:
            available = "Unknown"
        extras = []
        try:
            if discount and float(discount) > 0:
                extras.append(f"{discount}% off")
        except Exception:
            pass
        extras.append(f"{available} available")
        extra_info = " | ".join(extras)
        message = (
            f"You selected: {title}\n"
            f"Price: {price_str}\n"
            f"Type: {product_type}\n"
            f"Store: {store_name}\n"
            f"Availability: {extra_info}\n"
            f"Description: {description}\n\n"
            "Would you like to add this product to your cart? Please say 'add to cart' or 'no'."
        )
        
        dispatcher.utter_message(
            text=message,
            buttons=[
                {"title": "Add to cart", "payload": "Add to Cart"},
                {"title": "No", "payload": "No"}
            ]
        )

        return [SlotSet("selected_product", json.dumps(selected_product))]


class ActionAddToCart(Action):
    def name(self) -> Text:
        return "action_add_to_cart"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict]:

        # Get user_id; if not present or not a string, send blank string (not a user utterance!)
        user_id = tracker.get_slot("user_id")
        if user_id is None or not isinstance(user_id, str) or not user_id.isdigit():
            user_id = "253"

        selected_product_json = tracker.get_slot("selected_product")
        if not selected_product_json:
            dispatcher.utter_message(text="You have not selected a product to add. Please select a product first.")
            return []

        try:
            product = json.loads(selected_product_json)
        except Exception as e:
            print(f"[EXCEPTION] parsing selected product: {e}")
            dispatcher.utter_message(text="Sorry, I had trouble with your selected product.")
            return []

        product_id = product.get("product_id")
        shipper_id = product.get("shipper_id")
        if not product_id or not shipper_id:
            dispatcher.utter_message(text="Selected product information incomplete.")
            return []

        payload = {
            "user_id": user_id,
            "quantity": 1,
            "product_id": product_id,
            "shipper_id": shipper_id,
        }

        try:
            url = "https://stageshipperapi.thedelivio.com/api/add-product-to-cart"
            res = requests.post(url, json=payload, timeout=8)
            print(f"[API CALL] POST {url} - Status: {res.status_code}, Body: {payload}")
            resp_json = res.json()
            print(f"[API RESPONSE] {resp_json}")

            if res.status_code == 200 and resp_json.get("status") == 1:
                dispatcher.utter_message(text=f"{product.get('title', 'The product')} has been added to your cart.")
               
                dispatcher.utter_message(
                    text="Would you want to view your cart?",
                    buttons=[
                        {"title": "Yes", "payload": "View Cart"},
                        {"title": "No", "payload": "No"}
                    ]
                )
                
            else:
                msg = resp_json.get("message", "Could not add the product to your cart, please try again.")
                dispatcher.utter_message(text=msg)
        except Exception as e:
            print(f"[EXCEPTION] adding product to cart: {e}")
            dispatcher.utter_message(text="An error occurred while adding to cart.")
        return []


class ActionViewCart(Action):
    def name(self) -> Text:
        return "action_view_cart"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]
    ) -> List[Dict]:
        user_id = tracker.get_slot("user_id")
        # If user_id is not a digit string, send it as blank or 253 for testing
        if not (isinstance(user_id, str) and user_id.isdigit()):
            user_id = "253"

        payload = {"user_id": user_id, "coupon_id": ""}

        try:
            url = "https://stageshipperapi.thedelivio.com/api/cart-list"
            response = requests.post(url, json=payload, timeout=10)
            print(f"[API CALL] POST {url} - Status: {response.status_code} Body: {payload}")
            data = response.json()
            print("[API RESPONSE]", data)

            if response.status_code != 200 or data.get("status") != 1 or "data" not in data:
                msg = data.get("message", "Sorry, I couldn't retrieve your cart details right now.")
                dispatcher.utter_message(text=msg)
                return []

            cart_data = data["data"]
            cartlist = cart_data.get("cartlist", [])
            order_meta = cart_data.get("orderMetaData", {})

            if not cartlist:
                dispatcher.utter_message(text="Your cart is empty.")
                return []

            product_lines = []
            for item in cartlist:
                title = item.get("title", "Unnamed Product")
                qty = item.get("quantity", 1)
                try:
                    price = float(item.get("discounted_price", 0))
                except Exception:
                    price = item.get("price", "-")
                discount = item.get("discount", "0")
                line = f"- {title} (Qty: {qty}, Price: ${price}, Discount: {discount}%)"
                product_lines.append(line)

            payment_lines = []
            if order_meta:
                if "sub_total_amount" in order_meta:
                    payment_lines.append(f"Subtotal: ${order_meta.get('sub_total_amount')}")
                if "discount_amount" in order_meta and float(order_meta.get("discount_amount", 0)) > 0:
                    payment_lines.append(f"Discount: ${order_meta.get('discount_amount')}")
                if "tax" in order_meta and float(order_meta.get("tax", 0)) > 0:
                    payment_lines.append(f"Tax: ${order_meta.get('tax')}")
                if "total_delivery_charge" in order_meta and float(order_meta.get("total_delivery_charge", 0)) > 0:
                    payment_lines.append(f"Delivery Fee: ${order_meta.get('total_delivery_charge')}")
                if "total" in order_meta:
                    payment_lines.append(f"Cart Total: ${order_meta.get('total')}")

            cart_info = "\n".join(product_lines)
            payment_info = "\n".join(payment_lines)

            out_msg = f"Your cart:\n{cart_info}"
            if payment_info:
                out_msg += f"\n\n{payment_info}"

            #dispatcher.utter_message(text=out_msg)
            
            dispatcher.utter_message(
                text=out_msg,
                buttons=[
                    {"title": "View your Addresses", "payload": "View Address"},
                    {"title": "No", "payload": "No"}
                ]
            )

            # return [SlotSet("selected_product", json.dumps(selected_product))]
            
            return []
        except Exception as e:
            print(f"[EXCEPTION] in ActionViewCart: {e}")
            dispatcher.utter_message(text="Sorry, an error occurred while fetching your cart.")
            return []



class ActionCheckout(Action):
    def name(self) -> Text:
        return "action_checkout"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        user_id = tracker.get_slot("user_id")
        if not user_id:
            dispatcher.utter_message(text="Please log in to proceed to checkout.")
            return []
        payload = {"user_id": user_id}
        try:
            url = f"{API_BASE}/orders/create"
            response = requests.post(url, json=payload, timeout=8)
            print(f"[API CALL] POST {url} - Status: {response.status_code}")
            if response.status_code == 200 and response.json().get("success", False):
                dispatcher.utter_message(text="Your order has been placed successfully!")
            else:
                dispatcher.utter_message(text="Sorry, something went wrong while placing your order.")
        except Exception as e:
            print(f"[EXCEPTION] placing order: {e}")
            dispatcher.utter_message(text="An error occurred during checkout.")
        return []


class ActionTrackOrder(Action):
    def name(self) -> Text:
        return "action_track_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict]:
        order_id = next(tracker.get_latest_entity_values("order_id"), None)
        if not order_id:
            dispatcher.utter_message(text="Please provide your order ID to track it.")
            return []
        try:
            url = f"{API_BASE}/orders/{order_id}"
            response = requests.get(url, timeout=8)
            print(f"[API CALL] GET {url} - Status: {response.status_code}")
            if response.status_code == 200:
                status = response.json().get("status", "Unknown")
                dispatcher.utter_message(text=f"Status of your order {order_id} is: {status}.")
            else:
                dispatcher.utter_message(text="Sorry, I couldn't fetch the order status.")
        except Exception as e:
            print(f"[EXCEPTION] tracking order: {e}")
            dispatcher.utter_message(text="An error occurred while tracking your order.")
        return []

class ActionGetAddress(Action):

    def name(self) -> Text:
        return "action_get_address"

    def run(
        self,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict[Text, Any],
    ) -> List[Dict]:
        user_id = tracker.get_slot("user_id") or "253"
        shipper_id = ""
        address_id = 306

        payload = {
            "user_id": user_id,
            "shipper_id": shipper_id,
            "address_id": address_id,
        }

        try:
            url = "https://stageshipperapi.thedelivio.com/api/getAddress"
            response = requests.post(url, json=payload, timeout=8)
            resp_json = response.json()
            print(f"[API RESPONSE] {resp_json}")

            if (
                response.status_code != 200
                or resp_json.get("status") != 1
                or not resp_json.get("data")
            ):
                dispatcher.utter_message(text="Sorry, I couldn't fetch your address details right now.")
                return []

            data = resp_json.get("data", {})
            address_list = data.get("addressList")
            if not address_list or not isinstance(address_list, list) or not address_list:
                dispatcher.utter_message(text="No addresses found for your account.")
                return []

            address = address_list[0]  # pick first (or loop if you want to display all)
            
            title = address.get("address_name", "Home")
            name = address.get("name", "")
            line1 = address.get("address", "")
            line2 = address.get("address2", "")
            city = address.get("city", "")
            state = address.get("state", "")
            zipc = address.get("zip", "")
            country = address.get("country_name", "")
            phone = address.get("phone", "")
            address_msg = (
                f"🏠 **{title}**\n\n"
                f"{name}\n"
                f"{line1}" + (f"\n{line2}" if line2 else "") + "\n"
                f"{city}, {state}, {zipc}\n"
                f"{country}\n\n"
                f"📞 {phone}"
            )
            dispatcher.utter_message(text=address_msg)
            dispatcher.utter_message(
                text="Would you like to proceed with this address?",
                buttons=[{"title": "Pay Now", "payload": "Pay Now"}]
            )
            
            #lines = [f"{k.replace('_',' ').capitalize()}: {v}" for k, v in address.items()]
            #address_message = "\n".join(lines)

            #dispatcher.utter_message(
            #    text=f"Here is your address info:\n{address_message}\nYou can confirm to proceed to checkout or change the address."
            #)
            #dispatcher.utter_message(
            #    buttons=[{"title": "Pay Now", "payload": "Pay Now"}]
            #)
        except Exception as e:
            print(f"[EXCEPTION] in ActionGetAddress: {e}")
            dispatcher.utter_message(text="Sorry, an error occurred while retrieving your address.")
        return []


class ActionCreateStripeCheckout(Action):
    def name(self) -> Text:
        return "action_create_stripe_checkout"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, any]) -> List[Dict]:
        
        # Set your TEST secret key here
        stripe.api_key = "sk_test_hvOUO0fHa8UL59XCgdhFWKFb"  # Use your test secret key

        try:
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": "inr",
                        "product_data": {
                            "name": "Your Cart Payment",
                        },
                        "unit_amount": 10000,  # $100 in paise
                    },
                    "quantity": 1,
                }],
                mode="payment",
                success_url="https://stageshipperapi.thedelivio.com/api/bot-payment-status?status=success",
                cancel_url="https://stageshipperapi.thedelivio.com/api/bot-payment-status?status=cancel",
            )
            payment_url = session.url

            dispatcher.utter_message(text=f"Please complete your payment by clicking:\n")
            dispatcher.utter_message(text=f"[Pay with Stripe]({payment_url})")

            dispatcher.utter_message( 
                buttons=[
                    {"title": "Check Payment Status", "payload": "Paid?"}
                ]
            )
            
        except Exception as e:
            dispatcher.utter_message(text=f"Error creating payment session: {str(e)}")
        return []
        
class ActionCheckPaymentStatus(Action):
    def name(self) -> Text:
        return "action_check_payment_status"

    def run(self, dispatcher, tracker, domain):
        user_id = tracker.get_slot("user_id") or "0"
        order_id = tracker.get_slot("order_id") or "0"

        url = "https://stageshipperapi.thedelivio.com/api/bot-payment-status"
        payload = {
            "user_id": user_id,
            "order_id": order_id
        }

        print(f"[DEBUG] Calling payment status API with POST: {url}, Payload: {payload}")

        try:
            response = requests.post(url, json=payload, timeout=8)
            if response.status_code != 200:
                print(f"[ERROR] Non-200 status code: {response.status_code}, Response: {response.text}")
                dispatcher.utter_message(text="Sorry, I couldn't retrieve the payment status at the moment.")
                return []

            data = response.json()
            print(f"[DEBUG] Payment status API response JSON: {data}")

            payment_status = data.get("data", {}).get("payment_status", "").lower()
            if not payment_status:
                print("[WARN] 'payment_status' key missing or empty in response data.")
                dispatcher.utter_message(text="Sorry, I couldn't find your payment status.")
                return []

            if payment_status == "paid":
                dispatcher.utter_message(text="✅ Your payment was successful! Thank you.")
            elif payment_status == "failed":
                dispatcher.utter_message(text="❌ Your payment failed. Please try again or contact support.")
            else:
                dispatcher.utter_message(text="Your payment is still processing. Please wait and check again.")

        except Exception as e:
            print(f"[EXCEPTION] in ActionCheckPaymentStatus: {e}")
            dispatcher.utter_message(text="Sorry, I couldn't check your payment status right now.")

        return []

class ActionProductLLMSearch(Action):
    def name(self) -> str:
        return "action_product_llm_search"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: dict) -> list:

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            dispatcher.utter_message(text="OpenAI API key not configured. Please contact administrator.")
            return []

        user_query = tracker.latest_message.get("text", "").strip()
        if not user_query:
            dispatcher.utter_message(text="Please tell me what product or category you want to search for.")
            return []

        intent_name = tracker.latest_message.get("intent", {}).get("name")

        # Get current page slot (pagination)
        page = tracker.get_slot("search_page")
        try:
            page = int(page)
        except (TypeError, ValueError):
            page = 1

        # Get last search keyword slot (to maintain search term across pages)
        last_search_string = tracker.get_slot("last_search_string")

        if intent_name == "search_products":
            # New search: extract clean keyword and reset page
            match = re.search(
                r"(?:show me|show|find|get|search for|i want|can i have|need)\s+([\w\s]+)",
                user_query.lower()
            )
            if match:
                search_keyword = match.group(1).strip()
            else:
                search_keyword = user_query

            last_search_string = search_keyword
            page = 1  # reset page on new search

        elif intent_name == "search_products_next":
            # For pagination, use previously saved search keyword (slot)
            if not last_search_string:
                last_search_string = user_query  # fallback if slot empty

            # page should be incremented elsewhere (in pagination action)
        else:
            # fallback for other intents
            if not last_search_string:
                last_search_string = user_query

        print(f"[DEBUG] Using search string for backend API: '{last_search_string}', page: {page}")

        # Prepare payload for product search backend
        search_endpoint = f"{API_BASE}/getMasterProducts"
        payload = {
            "wh_account_id": "",
            "upc": "",
            "ai_category_id": "",
            "ai_product_id": "",
            "product_id": "",
            "search_string": last_search_string,
            "zipcode": "",
            "user_id": "",
            "page": str(page),
            "items": "5"
        }

        print(f"[DEBUG] Calling backend API with payload: {payload}")

        try:
            api_response = requests.post(search_endpoint, json=payload, timeout=8)
            api_response.raise_for_status()
            data = api_response.json()
            api_data = data.get("data", {}) if data else {}
            if isinstance(api_data, dict):
                products = api_data.get("getMasterProducts", [])
            else:
                products = api_data
            print(f"[DEBUG] Backend API returned {len(products)} products")
        except Exception as e:
            print(f"[WARN] Could not fetch products from backend: {e}")
            products = []

        # Filter only valid products (dicts)
        product_dicts = [p for p in products if isinstance(p, dict)] if isinstance(products, list) else []

        # Build user-friendly product list message
        BIG_BLANK = "\n\u2800\n"   # Unicode Braille blank!

        if product_dicts:
            product_lines = []
            for idx, p in enumerate(product_dicts):
                title = p.get('product_name') or p.get('title', 'Unnamed Product')
                price = p.get('discounted_price', p.get('product_price', '-'))
                price_str = f"₹{price}"
                desc = p.get('description', '').strip()
                desc_short = (desc[:80] + '...') if desc and len(desc) > 80 else desc

                # Each field is on its own line
                line = (
                    f"{idx+1}. **{title}**\n"
                    f"{price_str}\n"
                    f"{desc_short if desc_short else ''}"
                )
                product_lines.append(line.strip())

            message = (
                "🛒 **Products found:**\n\n"
                + "\n\n".join(product_lines) +
                "\n\n➡️ Reply with the product number to see details, or type 'next' to see more options."
            )

            buttons = [{"title": str(idx+1), "payload": str(idx+1)} for idx in range(len(product_dicts))]
            buttons.append({"title": "Next ▶️", "payload": "next"})
            dispatcher.utter_message(text=message, buttons=buttons)
        else:
            dispatcher.utter_message(
                text="😕 Sorry, I couldn't find any products for your search. Please try a different keyword or category."
            )








        # Persist slots for pagination and search string reuse
        if product_dicts:
            return [
                SlotSet("recent_products", json.dumps(product_dicts)),
                SlotSet("search_page", page),
                SlotSet("last_search_string", last_search_string)
            ]
        else:
            return [
                SlotSet("recent_products", None),
                SlotSet("search_page", 1),
                SlotSet("last_search_string", last_search_string)
            ]



class ActionNextProductPage(Action):
    def name(self) -> str:
        return "action_next_product_page"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: dict) -> list:

        page = tracker.get_slot("search_page")
        try:
            page = int(page) + 1 if page else 2
        except ValueError:
            page = 2

        print(f"[DEBUG] Incrementing search page slot to: {page}")

        # Update page slot, search action will use updated page on next call
        return [SlotSet("search_page", page)]


class ActionResetSearchPage(Action):
    def name(self) -> str:
        return "action_reset_search_page"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: dict) -> list:

        print("[DEBUG] Resetting search_page slot to 1 for new search.")
        return [SlotSet("search_page", 1)]

        
# Regex to validate US ZIP code format (5 digits)
ZIP_REGEX = re.compile(r"^\d{5}$")

class ValidateZipcodeForm(FormValidationAction):
    def name(self) -> Text:
        return "validate_zipcode_form"

    def validate_zipcode(
        self,
        slot_value: Any,
        dispatcher: CollectingDispatcher,
        tracker: Tracker,
        domain: Dict,
    ) -> Dict[Text, Any]:
        """Validate ZIP code"""

        if ZIP_REGEX.match(slot_value):
            return {"zipcode": slot_value}
        else:
            dispatcher.utter_message(text="That ZIP code doesn't seem valid. Please enter a proper 5-digit US ZIP code.")
            return {"zipcode": None}


class ActionGetNearestStore(Action):
    def name(self) -> Text:
        return "action_get_nearest_store"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict
    ) -> List[EventType]:

        zipcode = tracker.get_slot("zipcode")
        if not zipcode:
            # Ask for zipcode if missing
            dispatcher.utter_message(text="Please provide your 5-digit ZIP code first.")
            return []

        # Optional: get store search string if user mentioned
        latest_message = tracker.latest_message.get("text", "")
        # You can extract store name from entity or intent if NLU setup supports that, else pass empty
        store_search_string = None
        entities = tracker.latest_message.get("entities", [])
        for ent in entities:
            if ent.get("entity") == "store_name":
                store_search_string = ent.get("value")
                break

        # Call your getNearestStore API:
        api_url = "https://your-api-endpoint/getNearestStore"
        params = {
            "zipcode": zipcode,
            "search_string": store_search_string or ""
        }

        try:
            response = requests.get(api_url, params=params)
            response.raise_for_status()
            stores = response.json().get("stores", [])

            if not stores:
                dispatcher.utter_message(text="Sorry, no stores found near this ZIP code. Would you like to try a different ZIP code?")
                # Optionally reset ZIP or store slots
                return [SlotSet("selected_store", None)]

            # Store the list in tracker or return first page (for demo we show first 3)
            store_list_text = "Found these stores in your area:\n"
            for idx, store in enumerate(stores[:5], start=1):
                store_list_text += f"{idx}. {store.get('name')} - {store.get('address')}\n"

            dispatcher.utter_message(text=store_list_text)
            dispatcher.utter_message(text="Please select a store by typing its option number or name.")

            # Save the list in a slot or temp memory if you want to validate selection later
            # For now, return slot updated for selected_store as None to wait for next input
            return [SlotSet("stores_list", stores), SlotSet("selected_store", None)]

        except requests.RequestException:
            dispatcher.utter_message(text="Sorry, I am facing issues fetching stores right now. Please try again later.")
            return []


class ActionSetSelectedStore(Action):
    def name(self) -> Text:
        return "action_set_selected_store"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict
    ) -> List[EventType]:

        selected_text = tracker.latest_message.get("text")
        stores = tracker.get_slot("stores_list") or []

        if not stores:
            dispatcher.utter_message(text="I don't have any store list in memory, please search for stores first.")
            return []

        # Try to infer store choice from number or name matches
        selected_store = None
        # Check if user input is a digit within range
        if selected_text.isdigit():
            idx = int(selected_text) - 1
            if 0 <= idx < len(stores):
                selected_store = stores[idx]
        else:
            # Match by name case-insensitive substring
            for store in stores:
                if selected_text.lower() in store.get("name", "").lower():
                    selected_store = store
                    break

        if selected_store:
            dispatcher.utter_message(text=f"You have selected {selected_store.get('name')} located at {selected_store.get('address')}.")
            # Set slot with minimal store info - you can customize as per your store object structure
            return [SlotSet("selected_store", selected_store), SlotSet("store_context", True)]

        else:
            dispatcher.utter_message(text="Sorry, I couldn't find a matching store. Please try again or enter a different name.")
            return [SlotSet("selected_store", None)]

class ActionRecallPreviousLocation(Action):
    def name(self) -> Text:
        return "action_recall_previous_location"

    def run(
        self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict
    ) -> List[EventType]:
        last_zip = tracker.get_slot("last_zipcode")
        last_store = tracker.get_slot("selected_store")

        if last_zip and last_store:
            store_name = last_store.get("name") if isinstance(last_store, dict) else str(last_store)
            dispatcher.utter_message(text=f"Welcome back! Would you like to continue with your last ZIP code {last_zip} and store {store_name}?")
            # You may want to set a flag or slot to capture user's yes/no here for confirmation
            return []
        else:
            dispatcher.utter_message(text="Welcome! How can I assist you today?")
            return []
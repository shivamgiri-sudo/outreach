"""The website-form helper: a small script that draws a lead form and posts it to the connector.

The script holds only the public form key, never the secret capture token. Spam control: a hidden trap field, a minimum
fill time (the page sends when it drew the form) and an optional list of websites that may use the form.
"""
import json

SCRIPT = r"""(function () {
  var cfg = __CONFIG__;
  var me = document.currentScript;
  var box = document.getElementById("mcn-lead-form");
  if (!box) { box = document.createElement("div"); me.parentNode.insertBefore(box, me); }
  var started = Date.now();
  function el(tag, attrs, kids) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    (kids || []).forEach(function (kid) { node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid); });
    return node;
  }
  function field(name, label, type, required) {
    var id = "mcn-" + name;
    var input = el("input", { id: id, name: name, type: type || "text", autocomplete: name, maxlength: "200" });
    if (required) input.required = true;
    return el("p", {}, [el("label", { "for": id }, [label]), el("br"), input]);
  }
  var form = el("form", { novalidate: "novalidate", "aria-label": "Contact form" });
  form.appendChild(field("name", "Your name", "text", true));
  form.appendChild(field("phone", "Phone", "tel", true));
  form.appendChild(field("email", "Email", "email", false));
  form.appendChild(field("company", "Company", "text", false));
  var trap = el("p", { "aria-hidden": "true", style: "position:absolute;left:-9999px;height:0;overflow:hidden" },
    [el("label", { "for": "mcn-hp" }, ["Leave this empty"]), el("input", { id: "mcn-hp", name: "hp", tabindex: "-1", autocomplete: "off" })]);
  form.appendChild(trap);
  var agree = el("input", { id: "mcn-agree", type: "checkbox", required: "required" });
  form.appendChild(el("p", {}, [agree, el("label", { "for": "mcn-agree" }, [" I agree to be contacted by phone and email."])]));
  var wa = el("input", { id: "mcn-wa", type: "checkbox" });
  form.appendChild(el("p", {}, [wa, el("label", { "for": "mcn-wa" }, [" You may also contact me on WhatsApp."])]));
  var status = el("p", { role: "status", "aria-live": "polite" });
  var button = el("button", { type: "submit" }, ["Send"]);
  form.appendChild(button); form.appendChild(status);
  box.appendChild(form);
  form.addEventListener("submit", function (ev) {
    ev.preventDefault();
    if (!form.name.value.trim() || !(form.phone.value.trim() || form.email.value.trim()) || !agree.checked) {
      status.textContent = "Please add your name, a phone or email, and tick the box."; return;
    }
    var q = new URLSearchParams(location.search), body = new URLSearchParams();
    ["name", "phone", "email", "company"].forEach(function (k) { body.set(k, form[k].value); });
    body.set("hp", form.hp.value); body.set("consent", "yes"); body.set("whatsapp_optin", wa.checked ? "yes" : "no");
    body.set("_form_key", cfg.key); body.set("_t", String(started)); body.set("landing_url", location.href.split("#")[0]);
    body.set("referrer", document.referrer || "");
    ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "gclid", "fbclid"].forEach(function (k) { if (q.get(k)) body.set(k, q.get(k)); });
    button.disabled = true; status.textContent = "Sending...";
    fetch(cfg.endpoint, { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" }, body: body.toString() })
      .then(function (r) { if (!r.ok) throw new Error("bad"); status.textContent = cfg.thanks; form.reset(); })
      .catch(function () { button.disabled = false; status.textContent = "Sorry, that did not send. Please try again."; });
  });
})();
"""


def script_for(connector: dict, endpoint: str) -> str:
    config = {"endpoint": endpoint, "key": connector.get("form_key", ""),
              "thanks": connector.get("thanks_text") or "Thanks! We will be in touch shortly."}
    return SCRIPT.replace("__CONFIG__", json.dumps(config).replace("</", "<\\/"))


def snippet_for(embed_url: str) -> str:
    return f'<div id="mcn-lead-form"></div>\n<script src="{embed_url}" async></script>'

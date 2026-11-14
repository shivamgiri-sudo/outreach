import Icon from "../icons.jsx";

const VARIANT = { primary: "", secondary: "ghost", ghost: "quiet", danger: "danger", "danger-solid": "danger-solid" };
const cx = (...a) => a.filter(Boolean).join(" ");

// variant: primary | secondary | ghost | danger | danger-solid. size: md (40px) | sm (36px). Touch targets grow to 44px in CSS.
export function Button({ variant = "primary", size = "md", icon, className, children, type = "button", ...rest }) {
  return (
    <button type={type} className={cx("btn", VARIANT[variant], size === "sm" && "sm", className)} {...rest}>
      {icon && <Icon name={icon} size={size === "sm" ? 15 : 16} />}{children}
    </button>
  );
}

// Icon-only button: the label is required (it is the accessible name and the tooltip).
export function IconButton({ icon, label, size = "md", className, ...rest }) {
  return (
    <button type="button" className={cx("icon-btn", size === "sm" && "sm", className)} aria-label={label} title={label} {...rest}>
      <Icon name={icon} size={size === "sm" ? 16 : 18} />
    </button>
  );
}

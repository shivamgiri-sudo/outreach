// A small "?" that opens the guide chapter for the page you are on: <HelpLink chapter="funnel" label="Sales funnel" />.
// `topic` (optional) is a heading id inside the chapter. One component, so every page links the same way.
export default function HelpLink({ chapter, topic, label }) {
  const href = `#help/${chapter}${topic ? `/${topic}` : ""}`;
  return (
    <a className="help-q" href={href} data-testid={`help-link-${chapter}`} aria-label={`Help: ${label || chapter}`} title={`Open the guide: ${label || chapter}`}>?</a>
  );
}

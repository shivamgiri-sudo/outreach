import { Card, CardHeader, Badge } from "../ui/index.js";

// What Find & enrich's free steps added: a guessed email (kept apart from the real email, never sent to) with how it was
// checked, and the public Gravatar photo / profile. Shows nothing when neither is there.
const STATUS = {
  confirmed: ["success", "Mail server says it exists"],
  unconfirmed: ["neutral", "Not checked yet"],
  catch_all: ["warning", "Cannot be confirmed"],
  risky: ["warning", "Risky"],
  rejected: ["danger", "No address worked"],
};
const isUrl = (u) => /^https?:\/\//i.test(u || "");

export default function LeadGuess({ lead }) {
  const guess = lead.email_guess;
  const photo = isUrl(lead.photo_url) ? lead.photo_url : "";
  if (lead.email || (!guess && !photo)) return photo ? <GravatarOnly lead={lead} photo={photo} /> : null;
  const [tone, label] = STATUS[lead.email_guess_status] || STATUS.unconfirmed;
  return (
    <Card flat className="dsec lead-guess" data-testid="lead-guess">
      <CardHeader title="Guessed email" description="Built from the company's email format. It is never sent to; add it as the main email only once you have confirmed it." />
      <div className="lead-guess-row">
        {photo && <img className="lead-photo" src={photo} alt={`${lead.contact_name || "Contact"} (Gravatar photo)`} referrerPolicy="no-referrer" />}
        <div className="u-min0">
          {guess && <p className="lead-guess-email"><span className="lead-guess-addr">{guess}</span> <Badge tone={tone}>{label}</Badge></p>}
          {lead.email_guess_reason && <small className="sub">How: {lead.email_guess_reason}</small>}
          {isUrl(lead.gravatar_url) && <small className="sub lead-guess-link"><a href={lead.gravatar_url} target="_blank" rel="noopener noreferrer">Gravatar profile</a></small>}
        </div>
      </div>
    </Card>
  );
}

function GravatarOnly({ lead, photo }) {
  return (
    <Card flat className="dsec lead-guess" data-testid="lead-gravatar">
      <div className="lead-guess-row">
        <img className="lead-photo" src={photo} alt={`${lead.contact_name || "Contact"} (Gravatar photo)`} referrerPolicy="no-referrer" />
        <small className="sub">Photo from the public Gravatar profile of this email.
          {isUrl(lead.gravatar_url) && <> <a href={lead.gravatar_url} target="_blank" rel="noopener noreferrer">Open profile</a></>}</small>
      </div>
    </Card>
  );
}

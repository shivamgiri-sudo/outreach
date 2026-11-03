import { useState } from "react";
import { api, setToken } from "../api.js";

export default function Login({ onSignedIn }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    try {
      const { token } = await api.login(password);
      setToken(token);
      onSignedIn();
    } catch (err) {
      setError(err.message);
    }
  };

  return (
    <form onSubmit={submit} className="card" style={{ maxWidth: 360, margin: "15vh auto" }}>
      <h2>Outreach OS</h2>
      <p className="sub">Sign in to continue.</p>
      <label htmlFor="pw">Password</label>
      <input id="pw" type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
      {error && <p style={{ color: "var(--hot)" }}>{error}</p>}
      <button className="btn" style={{ marginTop: 12 }} type="submit" disabled={!password}>Sign in</button>
    </form>
  );
}

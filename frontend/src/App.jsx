
import { useCallback, useEffect, useRef, useState } from "react";
import "./App.css";

const API_BASE = "http://localhost:8000";

function getCookie(name) {
  const cookie = document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${name}=`));
  return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : "";
}

async function fetchAgentApi(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});

  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    let csrfToken = getCookie("csrftoken");
    if (!csrfToken) {
      const csrfResponse = await fetch(`${API_BASE}/api/auth/session/`, {
        credentials: "include",
      });
      if (!csrfResponse.ok) {
        throw new Error("Unable to prepare a secure agent request.");
      }
      csrfToken = getCookie("csrftoken");
    }
    if (!csrfToken) {
      throw new Error("Unable to prepare a secure agent request.");
    }
    headers.set("X-CSRFToken", csrfToken);
  }

  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
}

async function requestAgentQueue(status) {
  const query = status ? `?status=${encodeURIComponent(status)}` : "";
  const response = await fetchAgentApi(`/api/complaints/${query}`);
  const data = await response.json().catch(() => ({}));
  return { response, data };
}

function getDenoisingFeedback(response) {
  const denoisingStatus =
    typeof response?.denoising_status === "string"
      ? response.denoising_status
      : "";
  const transcriptionFailed =
    response?.transcription_status === "failed" ||
    response?.processing_status === "transcription_failed";
  const transcriptionCompleted =
    response?.transcription_status === "completed" ||
    (typeof response?.processing_status === "string" &&
      response.processing_status !== "transcription_failed");

  if (transcriptionFailed) {
    return {
      status: denoisingStatus,
      tone: "error",
      message: "Complaint saved, but transcription failed.",
    };
  }

  if (denoisingStatus === "failed") {
    return {
      status: denoisingStatus,
      tone: transcriptionCompleted ? "warning" : "info",
      message: transcriptionCompleted
        ? "Denoising failed. The original recording was used, and your complaint was still processed."
        : "Audio denoising failed.",
    };
  }

  if (denoisingStatus === "completed") {
    return {
      status: denoisingStatus,
      tone: "success",
      message: "Audio denoising completed successfully.",
    };
  }

  if (denoisingStatus) {
    return {
      status: denoisingStatus,
      tone: "info",
      message: "",
    };
  }

  return null;
}

function formatDenoisingStatus(status) {
  return status.replaceAll("_", " ");
}

function AgentReplyHistory({ replies }) {
  if (!Array.isArray(replies) || replies.length === 0) {
    return null;
  }

  return (
    <div className="agent-reply-history">
      <h4>Agent replies</h4>
      {replies.map((reply) => (
        <article className="agent-reply" key={reply.id}>
          {reply.translated_text && (
            <p>
              <strong>Reply:</strong> {reply.translated_text}
            </p>
          )}
          {reply.translation_status === "failed" && (
            <p className="field-help">
              Reply translation is unavailable.
            </p>
          )}
          {reply.audio_url ? (
            <audio
              controls
              preload="none"
              src={reply.audio_url}
              aria-label="Play agent reply"
            >
              Your browser does not support audio playback.
            </audio>
          ) : (
            <p className="field-help">
              Audio reply is unavailable
              {reply.audio_status
                ? ` (${reply.audio_status.replaceAll("_", " ")})`
                : ""}
              .
            </p>
          )}
        </article>
      ))}
    </div>
  );
}

function App() {
  const [activePortal, setActivePortal] = useState("customer");
  const [agentUser, setAgentUser] = useState(null);
  const [agentSessionLoading, setAgentSessionLoading] = useState(false);
  const [agentAuthError, setAgentAuthError] = useState("");
  const [agentUsername, setAgentUsername] = useState("");
  const [agentPassword, setAgentPassword] = useState("");
  const [agentLoginLoading, setAgentLoginLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [ticket, setTicket] = useState(null);
  const [language, setLanguage] = useState("te");
  const [phone, setPhone] = useState("");
  const [recording, setRecording] = useState(false);
  const [audioUrl, setAudioUrl] = useState("");
  const [photo, setPhoto] = useState(null);
  const [message, setMessage] = useState("");
  const [lookupTicketNumber, setLookupTicketNumber] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupError, setLookupError] = useState("");
  const [lookupResult, setLookupResult] = useState(null);
  const [queueStatus, setQueueStatus] = useState("");
  const [queueTickets, setQueueTickets] = useState([]);
  const [queueLoading, setQueueLoading] = useState(true);
  const [queueUpdatingTicket, setQueueUpdatingTicket] = useState("");
  const [queueError, setQueueError] = useState("");
  const [queueMessage, setQueueMessage] = useState("");
  const [queueStatusDrafts, setQueueStatusDrafts] = useState({});
  const [replyDrafts, setReplyDrafts] = useState({});
  const [replySubmittingTicket, setReplySubmittingTicket] = useState("");
  const ticketDenoisingFeedback = getDenoisingFeedback(ticket);
  const lookupDenoisingFeedback = getDenoisingFeedback(lookupResult);
  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const chunksRef = useRef([]);

  const loadAgentQueue = useCallback(async () => {
    setQueueLoading(true);
    setQueueError("");
    setQueueMessage("");

    try {
      const { response, data } = await requestAgentQueue(queueStatus);

      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          setAgentUser(null);
          setAgentAuthError("Your agent session has expired. Please sign in again.");
          return;
        }
        throw new Error(
          data.error || "Unable to load the ticket queue. Please try again."
        );
      }

      setQueueTickets(data.complaints || []);
    } catch (error) {
      setQueueError(
        error.message || "Unable to connect to the server. Please try again."
      );
    } finally {
      setQueueLoading(false);
    }
  }, [queueStatus]);

  useEffect(() => {
    if (activePortal !== "agent") {
      return undefined;
    }

    let isCurrent = true;

    async function checkAgentSession() {
      setAgentSessionLoading(true);
      setAgentAuthError("");
      try {
        const response = await fetch(`${API_BASE}/api/auth/session/`, {
          credentials: "include",
        });
        const data = await response.json().catch(() => ({}));

        if (!response.ok) {
          throw new Error(data.error || "Unable to check agent login.");
        }
        if (isCurrent) {
          setAgentUser(data.authenticated ? data.user : null);
          if (!data.authenticated) {
            setQueueTickets([]);
          }
        }
      } catch (error) {
        if (isCurrent) {
          setAgentUser(null);
          setAgentAuthError(error.message || "Unable to check agent login.");
        }
      } finally {
        if (isCurrent) {
          setAgentSessionLoading(false);
        }
      }
    }

    checkAgentSession();
    return () => {
      isCurrent = false;
    };
  }, [activePortal]);

  useEffect(() => {
    if (activePortal !== "agent" || !agentUser?.is_staff) {
      return undefined;
    }

    let isCurrent = true;
    async function refreshQueueForFilter() {
      setQueueLoading(true);
      setQueueError("");
      setQueueMessage("");
      try {
        const { response, data } = await requestAgentQueue(queueStatus);
        if (!response.ok) {
          if (response.status === 401 || response.status === 403) {
            setAgentUser(null);
            setAgentAuthError(
              "Your agent session has expired. Please sign in again."
            );
            return;
          }
          throw new Error(
            data.error || "Unable to load the ticket queue. Please try again."
          );
        }
        if (isCurrent) {
          setQueueTickets(data.complaints || []);
        }
      } catch (error) {
        if (isCurrent) {
          setQueueError(
            error.message || "Unable to connect to the server. Please try again."
          );
        }
      } finally {
        if (isCurrent) {
          setQueueLoading(false);
        }
      }
    }
    refreshQueueForFilter();
    return () => {
      isCurrent = false;
    };
  }, [activePortal, agentUser, queueStatus]);

  async function handleAgentLogin(event) {
    event.preventDefault();
    setAgentLoginLoading(true);
    setAgentAuthError("");

    try {
      const response = await fetchAgentApi("/api/auth/login/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: agentUsername,
          password: agentPassword,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "Unable to sign in.");
      }

      setAgentUser(data.user);
      setAgentPassword("");
      setQueueLoading(true);
    } catch (error) {
      setAgentAuthError(error.message || "Unable to connect to the server.");
    } finally {
      setAgentLoginLoading(false);
    }
  }

  async function handleAgentLogout() {
    setAgentAuthError("");
    try {
      const response = await fetchAgentApi("/api/auth/logout/", {
        method: "POST",
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(data.error || "Unable to sign out.");
      }
      setAgentUser(null);
      setQueueTickets([]);
      setQueueMessage("");
      setQueueError("");
    } catch (error) {
      setAgentAuthError(error.message || "Unable to connect to the server.");
    }
  }

  async function updateQueueTicketStatus(complaint) {
    const status =
      queueStatusDrafts[complaint.ticket_number] || complaint.status;
    if (status === complaint.status || queueUpdatingTicket) {
      return;
    }

    setQueueUpdatingTicket(complaint.ticket_number);
    setQueueError("");
    setQueueMessage("");

    try {
      const response = await fetchAgentApi(
        `/api/complaints/${encodeURIComponent(
          complaint.ticket_number
        )}/status/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/x-www-form-urlencoded",
          },
          body: new URLSearchParams({ status }),
        }
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          setAgentUser(null);
          setAgentAuthError("Your agent session has expired. Please sign in again.");
        }
        throw new Error(
          data.error || "Unable to update this ticket. Please try again."
        );
      }

      setQueueTickets((currentTickets) =>
        currentTickets.map((ticketItem) =>
          ticketItem.ticket_number === complaint.ticket_number
            ? {
                ...ticketItem,
                status: data.status,
                updated_at: data.updated_at,
              }
            : ticketItem
        )
      );
      setQueueStatusDrafts((drafts) => ({
        ...drafts,
        [complaint.ticket_number]: data.status,
      }));
      setQueueMessage("Ticket status updated.");
    } catch (error) {
      setQueueError(
        error.message || "Unable to connect to the server. Please try again."
      );
    } finally {
      setQueueUpdatingTicket("");
    }
  }

  async function submitAgentReply(event, complaint) {
    event.preventDefault();
    if (replySubmittingTicket) {
      return;
    }

    const englishText = (replyDrafts[complaint.ticket_number] || "").trim();
    if (!englishText) {
      setQueueError("Enter an English reply before submitting.");
      return;
    }

    setReplySubmittingTicket(complaint.ticket_number);
    setQueueError("");
    setQueueMessage("");

    try {
      const response = await fetchAgentApi(
        `/api/complaints/${encodeURIComponent(
          complaint.ticket_number
        )}/replies/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ english_text: englishText }),
        }
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          setAgentUser(null);
          setAgentAuthError("Your agent session has expired. Please sign in again.");
        }
        throw new Error(
          data.error || "Unable to save this reply. Please try again."
        );
      }

      const savedReply = data.reply;
      setQueueTickets((currentTickets) =>
        currentTickets.map((ticketItem) =>
          ticketItem.ticket_number === complaint.ticket_number
            ? {
                ...ticketItem,
                agent_replies: [
                  ...(Array.isArray(ticketItem.agent_replies)
                    ? ticketItem.agent_replies
                    : []),
                  savedReply,
                ],
              }
            : ticketItem
        )
      );
      setLookupResult((current) =>
        current?.ticket_number === complaint.ticket_number
          ? {
              ...current,
              agent_replies: [
                ...(Array.isArray(current.agent_replies)
                  ? current.agent_replies
                  : []),
                savedReply,
              ],
            }
          : current
      );
      setReplyDrafts((drafts) => ({
        ...drafts,
        [complaint.ticket_number]: "",
      }));
      setQueueMessage(
        data.message || "Reply saved. Check its translation and audio status."
      );
    } catch (error) {
      setQueueError(
        error.message || "Unable to connect to the server. Please try again."
      );
    } finally {
      setReplySubmittingTicket("");
    }
  }

  async function handleTicketLookup(event) {
    event.preventDefault();
    if (lookupLoading) {
      return;
    }

    setLookupLoading(true);
    setLookupError("");
    setLookupResult(null);

    try {
      const response = await fetch(
        `${API_BASE}/api/complaints/${encodeURIComponent(
          lookupTicketNumber.trim()
        )}/`
      );
      const data = await response.json().catch(() => ({}));

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error("We couldn't find a ticket with that number.");
        }
        throw new Error(
          data.error || "Unable to retrieve the ticket. Please try again."
        );
      }

      setLookupResult(data);
    } catch (error) {
      setLookupError(
        error.message || "Unable to connect to the server. Please try again."
      );
    } finally {
      setLookupLoading(false);
    }
  }

  async function startRecording() {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });

      streamRef.current = stream;
      chunksRef.current = [];

      const recorder = new MediaRecorder(stream);
      recorderRef.current = recorder;

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          chunksRef.current.push(event.data);
        }
      };

      recorder.onstop = () => {
        const audioBlob = new Blob(chunksRef.current, {
          type: recorder.mimeType,
        });

        setAudioUrl(URL.createObjectURL(audioBlob));
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
      };

      recorder.start();
      setRecording(true);
      setMessage("");
    } catch {
      setMessage(
        "Microphone access failed. Please allow microphone permission or try another browser."
      );
    }
  }

  function stopRecording() {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
      setRecording(false);
    }
  }



  async function handleSubmit(event) {
    event.preventDefault();

    if (loading) {
      return;
    }

    if (!/^[0-9]{10}$/.test(phone)) {
      setMessage("Please enter a valid 10-digit phone number.");
      return;
    }

    if (!audioUrl) {
      setMessage("Please record your complaint before submitting.");
      return;
    }

    setLoading(true);
    setMessage("");
    setTicket(null);

    try {

      const audioBlob = await fetch(audioUrl).then((response) =>
        response.blob()
      );

      const formData = new FormData();

      formData.append("customer_phone", phone);
      formData.append("language", language);
      formData.append(
        "audio",
        audioBlob,
        `complaint.${audioBlob.type.includes("webm") ? "webm" : "audio"}`
      );

      if (photo) {
        formData.append("photo", photo);
      }

      const response = await fetch(
        `${API_BASE}/api/complaints/`,
        {
          method: "POST",
          body: formData,
        }
      );


      const data = await response.json();

      if (!response.ok) {
        throw new Error(
          data.error || "Could not submit your complaint."
        );
      }

      setTicket(data);
      setMessage(
        data.message || "Your complaint has been submitted successfully!"
      );

      // Clear the form fields after successful submission.
      setPhone("");
      setLanguage("te");
      setPhoto(null);

      // Release the old audio preview and clear it.
      if (audioUrl) {
        URL.revokeObjectURL(audioUrl);
      }
      setAudioUrl("");

      // Reset the file input visually.
      const photoInput = document.getElementById("photo");
      if (photoInput) {
        photoInput.value = "";
      }
    } catch (error) {
      setMessage(
        error.message ||
        "Unable to connect to the server. Please try again."
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#home">
          <span className="brand-icon">V</span>
          VoiceDesk
        </a>
        <nav className="portal-switcher" aria-label="Choose portal view">
          <button
            className={activePortal === "customer" ? "is-active" : ""}
            type="button"
            aria-pressed={activePortal === "customer"}
            onClick={() => setActivePortal("customer")}
          >
            Customer Portal
          </button>
          <button
            className={activePortal === "agent" ? "is-active" : ""}
            type="button"
            aria-pressed={activePortal === "agent"}
            onClick={() => {
              setQueueLoading(true);
              setAgentSessionLoading(true);
              setAgentAuthError("");
              setActivePortal("agent");
            }}
          >
            Agent Portal
          </button>
        </nav>
      </header>

      <main className="page-content" id="home">
        <section className="intro">
          <span className="eyebrow">
            {activePortal === "customer" ? "CUSTOMER PORTAL" : "AGENT PORTAL"}
          </span>
          <h1>
            {activePortal === "customer"
              ? "Tell us what happened."
              : "Manage customer complaints."}
          </h1>
          <p>
            {activePortal === "customer"
              ? "Submit a complaint in your preferred language, track its status, and listen to replies."
              : "Sign in to review complaints, update ticket status, and send translated replies."}
          </p>
        </section>

        {activePortal === "customer" && (
        <section className="complaint-card ticket-lookup">
          <div className="card-heading">
            <div>
              <h2>Find an existing ticket</h2>
              <p>Enter your ticket UUID to check its status and transcript.</p>
            </div>
          </div>

          <form onSubmit={handleTicketLookup}>
            <div className="field">
              <label htmlFor="ticket-uuid">Ticket UUID</label>
              <input
                id="ticket-uuid"
                type="text"
                value={lookupTicketNumber}
                onChange={(event) =>
                  setLookupTicketNumber(event.target.value)
                }
                placeholder="Enter your ticket UUID"
                required
              />
            </div>
            <button
              className="button button-primary"
              type="submit"
              disabled={lookupLoading}
            >
              {lookupLoading ? "Finding ticket..." : "Find Ticket"}
            </button>
          </form>

          {lookupError && (
            <div className="message lookup-error" role="alert">
              {lookupError}
            </div>
          )}

          {lookupResult && (
            <div className="message lookup-result">
              <h3>Ticket details</h3>
              <p><strong>Ticket number:</strong> {lookupResult.ticket_number}</p>
              <p><strong>Status:</strong> {lookupResult.status}</p>
              {lookupDenoisingFeedback && (
                <div
                  className={`denoising-feedback denoising-feedback-${lookupDenoisingFeedback.tone}`}
                  role={
                    lookupDenoisingFeedback.tone === "error"
                      ? "alert"
                      : "status"
                  }
                  aria-live="polite"
                >
                  {lookupDenoisingFeedback.message && (
                    <p>{lookupDenoisingFeedback.message}</p>
                  )}
                  <p>
                    <strong>Denoising status:</strong>{" "}
                    {formatDenoisingStatus(lookupDenoisingFeedback.status)}
                  </p>
                </div>
              )}
              <AgentReplyHistory replies={lookupResult.agent_replies} />
              <p>
                <strong>Voice transcription:</strong>{" "}
                {typeof lookupResult.original_transcript === "string" &&
                lookupResult.original_transcript.trim()
                  ? lookupResult.original_transcript
                  : "No transcript available."}
              </p>
              <p>
                <strong>Created:</strong>{" "}
                {new Date(lookupResult.created_at).toLocaleString()}
              </p>
              <p>
                <strong>Updated:</strong>{" "}
                {new Date(lookupResult.updated_at).toLocaleString()}
              </p>
              {lookupResult.possible_duplicate &&
                lookupResult.possible_duplicate_ticket_number && (
                  <p className="possible-duplicate">
                    <strong>Possible duplicate:</strong>{" "}
                    {lookupResult.possible_duplicate_ticket_number}
                  </p>
                )}
            </div>
          )}
        </section>
        )}

        {activePortal === "agent" && (
          agentSessionLoading ? (
            <section className="complaint-card" role="status">
              Checking agent session...
            </section>
          ) : agentUser?.is_staff ? (
        <section className="complaint-card agent-queue">
          <div className="card-heading">
            <div>
              <h2>Agent ticket queue</h2>
              <p>Signed in as {agentUser.username}</p>
            </div>
            <div className="agent-actions">
              <button
                className="button button-secondary"
                type="button"
                onClick={handleAgentLogout}
              >
                Sign out
              </button>
              <button
                className="button button-primary"
                type="button"
                onClick={loadAgentQueue}
                disabled={queueLoading}
              >
                {queueLoading ? "Refreshing..." : "Refresh"}
              </button>
            </div>
          </div>

          <div className="field queue-filter">
            <label htmlFor="queue-status-filter">Filter by status</label>
            <select
              id="queue-status-filter"
              value={queueStatus}
              onChange={(event) => {
                setQueueLoading(true);
                setQueueStatus(event.target.value);
              }}
            >
              <option value="">All statuses</option>
              <option value="submitted">Submitted</option>
              <option value="processing">Processing</option>
              <option value="open">Open</option>
              <option value="in_progress">In Progress</option>
              <option value="resolved">Resolved</option>
              <option value="closed">Closed</option>
            </select>
          </div>

          {queueError && (
            <div className="message lookup-error" role="alert">
              {queueError}
            </div>
          )}
          {queueMessage && (
            <div className="message" role="status">
              {queueMessage}
            </div>
          )}
          {agentAuthError && (
            <div className="message lookup-error" role="alert">
              {agentAuthError}
            </div>
          )}

          {!queueLoading && !queueError && queueTickets.length === 0 && (
            <p className="field-help">No tickets match this filter.</p>
          )}

          <div className="queue-list">
            {queueTickets.map((queueTicket) => (
              <article
                className="queue-ticket"
                key={queueTicket.ticket_number}
              >
                <div className="queue-ticket-heading">
                  <strong>{queueTicket.ticket_number}</strong>
                  <span className="step-label">{queueTicket.status}</span>
                </div>
                {queueTicket.possible_duplicate &&
                  queueTicket.possible_duplicate_ticket_number && (
                    <p className="possible-duplicate">
                      <strong>Possible duplicate of:</strong>{" "}
                      {queueTicket.possible_duplicate_ticket_number}
                    </p>
                  )}
                <p>
                  <strong>Language:</strong> {queueTicket.language}
                  {" · "}
                  <strong>Processing:</strong>{" "}
                  {queueTicket.processing_status.replaceAll("_", " ")}
                </p>
                {queueTicket.classification_status === "suggested" && (
                  <p>
                    <strong>Automated suggestions (unreviewed)</strong>
                    {" · "}
                    <strong>Category:</strong>{" "}
                    {queueTicket.category || "Uncategorized"}
                    {" · "}
                    <strong>Priority:</strong> {queueTicket.priority}
                  </p>
                )}
                <p>
                  <strong>
                    {queueTicket.classification_status === "suggested"
                      ? "Suggested summary:"
                      : "Summary:"}
                  </strong>{" "}
                  {queueTicket.summary || "No summary available."}
                </p>
                <p>
                  <strong>Voice transcription:</strong>{" "}
                  {queueTicket.original_transcript || "No transcript available."}
                </p>
                {queueTicket.english_translation && (
                  <p>
                    <strong>English translation:</strong>{" "}
                    {queueTicket.english_translation}
                  </p>
                )}
                <AgentReplyHistory replies={queueTicket.agent_replies} />
                <form
                  className="agent-reply-form"
                  onSubmit={(event) => submitAgentReply(event, queueTicket)}
                >
                  <label htmlFor={`agent-reply-${queueTicket.ticket_number}`}>
                    Agent reply in English
                  </label>
                  <textarea
                    id={`agent-reply-${queueTicket.ticket_number}`}
                    value={replyDrafts[queueTicket.ticket_number] || ""}
                    onChange={(event) =>
                      setReplyDrafts((drafts) => ({
                        ...drafts,
                        [queueTicket.ticket_number]: event.target.value,
                      }))
                    }
                    rows={3}
                    required
                  />
                  <button
                    className="button button-primary"
                    type="submit"
                    disabled={Boolean(replySubmittingTicket)}
                  >
                    {replySubmittingTicket === queueTicket.ticket_number
                      ? "Saving reply..."
                      : "Send translated reply"}
                  </button>
                </form>
                <p>
                  <strong>Created:</strong>{" "}
                  {new Date(queueTicket.created_at).toLocaleString()}
                  {" · "}
                  <strong>Updated:</strong>{" "}
                  {new Date(queueTicket.updated_at).toLocaleString()}
                </p>
                <div className="queue-status-control">
                  <label htmlFor={`queue-status-${queueTicket.ticket_number}`}>
                    Update status
                  </label>
                  <select
                    id={`queue-status-${queueTicket.ticket_number}`}
                    value={
                      queueStatusDrafts[queueTicket.ticket_number] ||
                      queueTicket.status
                    }
                    onChange={(event) =>
                      setQueueStatusDrafts((drafts) => ({
                        ...drafts,
                        [queueTicket.ticket_number]: event.target.value,
                      }))
                    }
                  >
                    <option value="submitted">Submitted</option>
                    <option value="processing">Processing</option>
                    <option value="open">Open</option>
                    <option value="in_progress">In Progress</option>
                    <option value="resolved">Resolved</option>
                    <option value="closed">Closed</option>
                  </select>
                  <button
                    className="button button-primary"
                    type="button"
                    onClick={() => updateQueueTicketStatus(queueTicket)}
                    disabled={
                      queueLoading ||
                      Boolean(queueUpdatingTicket) ||
                      (queueStatusDrafts[queueTicket.ticket_number] ||
                        queueTicket.status) === queueTicket.status
                    }
                  >
                    {queueUpdatingTicket === queueTicket.ticket_number
                      ? "Saving..."
                      : "Update"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        </section>
          ) : (
            <section className="complaint-card agent-login">
              <div className="card-heading">
                <div>
                  <h2>Agent sign in</h2>
                  <p>Use an authorized Django staff account to access the queue.</p>
                </div>
              </div>
              {agentAuthError && (
                <div className="message lookup-error" role="alert">
                  {agentAuthError}
                </div>
              )}
              <form onSubmit={handleAgentLogin}>
                <div className="field">
                  <label htmlFor="agent-username">Username</label>
                  <input
                    id="agent-username"
                    autoComplete="username"
                    value={agentUsername}
                    onChange={(event) => setAgentUsername(event.target.value)}
                    required
                  />
                </div>
                <div className="field">
                  <label htmlFor="agent-password">Password</label>
                  <input
                    id="agent-password"
                    type="password"
                    autoComplete="current-password"
                    value={agentPassword}
                    onChange={(event) => setAgentPassword(event.target.value)}
                    required
                  />
                </div>
                <button
                  className="button button-primary"
                  type="submit"
                  disabled={agentLoginLoading}
                >
                  {agentLoginLoading ? "Signing in..." : "Sign in"}
                </button>
              </form>
              <p className="field-help">
                Demo UI separation only; access is enforced by the backend.
              </p>
            </section>
          )
        )}

        {activePortal === "customer" && (
        <form className="complaint-card" onSubmit={handleSubmit}>
          <div className="card-heading">
            <div>
              <h2>Submit a complaint</h2>
              <p>Fill in your details and record your voice message.</p>
            </div>
            <span className="step-label">STEP 1 OF 3</span>
          </div>

          <div className="field">
            <label htmlFor="language">Preferred language</label>
            <select
              id="language"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
            >
              <option value="te">Telugu</option>
              <option value="hi">Hindi</option>
              <option value="en">English</option>
              <option value="ta">Tamil</option>
              <option value="kn">Kannada</option>
            </select>
            <span className="field-help">
              Choose the language you'll speak in.
            </span>
          </div>

          <div className="field">
            <label htmlFor="phone">Mobile number</label>
            <input
              id="phone"
              type="tel"
              inputMode="numeric"
              autoComplete="tel-national"
              placeholder="Enter your 10-digit number"
              value={phone}
              maxLength={10}
              onChange={(event) =>
                setPhone(event.target.value.replace(/\D/g, ""))
              }
            />
            <span className="field-help">
              Phone verification will be added in a later step.
            </span>
          </div>

          <div className="field">
            <label>Voice complaint</label>
            <div className={`record-box ${recording ? "is-recording" : ""}`}>
              <div className="mic-icon">
                {recording ? "●" : "🎙"}
              </div>
              <h3>{recording ? "Recording in progress" : "Your voice matters"}</h3>
              <p>
                {recording
                  ? "Speak clearly. Press stop when you're finished."
                  : "Tap the button and explain your complaint in your language."}
              </p>

              {!recording ? (
                <button
                  className="button button-primary"
                  type="button"
                  onClick={startRecording}
                >
                  Start recording
                </button>
              ) : (
                <button
                  className="button button-stop"
                  type="button"
                  onClick={stopRecording}
                >
                  Stop recording
                </button>
              )}

              {audioUrl && !recording && (
                <div className="audio-preview">
                  <p>Recording ready</p>
                  <audio controls src={audioUrl}>
                    Your browser does not support audio playback.
                  </audio>
                  <button
                    type="button"
                    className="text-button"
                    onClick={() => {
                      URL.revokeObjectURL(audioUrl);
                      setAudioUrl("");
                    }}
                  >
                    Record again
                  </button>
                </div>
              )}
            </div>
          </div>

          <div className="field">
            <label htmlFor="photo">Supporting photo (optional)</label>
            <input
              id="photo"
              type="file"
              accept="image/*"
              onChange={(event) =>
                setPhoto(event.target.files?.[0] || null)
              }
            />
            {photo && <span className="field-help">Selected: {photo.name}</span>}
          </div>


          {message && !ticket && (
            <div className="message" role="status">
              {message}
            </div>
          )}

          {ticket && (
            <div className="message">
              <h3>Complaint registered!</h3>
              <p>Ticket number: {ticket.ticket_number}</p>
              <p>Status: {ticket.status}</p>
              {ticketDenoisingFeedback && (
                <div
                  className={`denoising-feedback denoising-feedback-${ticketDenoisingFeedback.tone}`}
                  role={
                    ticketDenoisingFeedback.tone === "error"
                      ? "alert"
                      : "status"
                  }
                  aria-live="polite"
                >
                  {ticketDenoisingFeedback.message && (
                    <p>{ticketDenoisingFeedback.message}</p>
                  )}
                  {ticketDenoisingFeedback.status && (
                    <p>
                      <strong>Denoising status:</strong>{" "}
                      {formatDenoisingStatus(ticketDenoisingFeedback.status)}
                    </p>
                  )}
                </div>
              )}
              {ticket.possible_duplicate &&
                ticket.possible_duplicate_ticket_number && (
                  <p className="possible-duplicate">
                    <strong>Possible duplicate of:</strong>{" "}
                    {ticket.possible_duplicate_ticket_number}
                  </p>
                )}
              {ticket.processing_status && (
                <p>
                  Processing: {ticket.processing_status.replaceAll("_", " ")}
                </p>
              )}
              {typeof ticket.original_transcript === "string" &&
                ticket.original_transcript.trim() && (
                  <p>
                    <strong>Voice transcription:</strong>{" "}
                    {ticket.original_transcript}
                  </p>
                )}
              {ticket.classification_status === "suggested" && (
                <>
                  <p><strong>Automated suggestions (unreviewed)</strong></p>
                  <p>Category: {ticket.category}</p>
                  <p>Priority: {ticket.priority}</p>
                  <p>Summary: {ticket.summary}</p>
                </>
              )}
              {ticket.processing_status === "translation_unavailable" && (
                <p>English translation is unavailable until a provider is configured.</p>
              )}
              {ticket.english_translation && (
                <p>
                  <strong>English translation:</strong>{" "}
                  {ticket.english_translation}
                </p>
              )}
              <AgentReplyHistory replies={ticket.agent_replies} />
            </div>
          )}


          <button
            className="button button-submit"
            type="submit"
            disabled={loading}
          >
            {loading ? "Submitting..." : "Submit complaint →"}
          </button>

          <p className="privacy-note">
            Demo only. Do not submit sensitive personal information.
          </p>
        </form>
        )}

        <footer className="footer">
          VoiceDesk · Multilingual support for everyone
        </footer>
      </main>
    </div>
  );
}

export default App;

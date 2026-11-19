const storageKey = 'onto-paas-conversation-sessions-v1';
const sessions = new Map();

function sessionFlags(value) {
  return {
    pinned: value?.pinned === true,
    unread: value?.unread === true,
    deleted: value?.deleted === true,
  };
}

try {
  const stored = JSON.parse(localStorage.getItem(storageKey));
  if (stored && typeof stored === 'object' && !Array.isArray(stored)) {
    for (const [id, flags] of Object.entries(stored)) {
      if (id.trim()) sessions.set(id, sessionFlags(flags));
    }
  }
} catch {
  // Preferences remain available for this page when browser storage is unavailable.
}

export function conversationSession(id) {
  return sessionFlags(sessions.get(id));
}

export function updateConversationSession(id, patch) {
  const next = conversationSession(id);
  if (typeof id !== 'string' || !id.trim() || !patch || typeof patch !== 'object') return next;

  for (const flag of Object.keys(next)) {
    if (Object.hasOwn(patch, flag) && typeof patch[flag] === 'boolean') next[flag] = patch[flag];
  }
  if (Object.values(next).some(Boolean)) sessions.set(id, next);
  else sessions.delete(id);

  try {
    localStorage.setItem(storageKey, JSON.stringify(Object.fromEntries(sessions)));
  } catch {
    // Keep the user's changes in memory if the browser cannot persist them.
  }
  return { ...next };
}

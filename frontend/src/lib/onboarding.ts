let seenInMemory = false

export function hasSeenTour() {
  try {
    return seenInMemory || localStorage.getItem('dr_welcome_tour_seen') === '1'
  } catch {
    return seenInMemory
  }
}

export function markTourSeen() {
  seenInMemory = true
  try {
    localStorage.setItem('dr_welcome_tour_seen', '1')
  } catch {
    // Session memory remains usable when browser storage is blocked.
  }
}

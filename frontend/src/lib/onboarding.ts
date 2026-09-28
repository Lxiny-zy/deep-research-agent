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

// 欢迎光环：每个浏览器会话展示一次（新开标签页 / 重新打开浏览器会再看到）
let introInMemory = false

export function hasSeenIntro() {
  try {
    return introInMemory || sessionStorage.getItem('sr_intro_seen') === '1'
  } catch {
    return introInMemory
  }
}

export function markIntroSeen() {
  introInMemory = true
  try {
    sessionStorage.setItem('sr_intro_seen', '1')
  } catch {
    // 存储不可用时本页内仍然只展示一次
  }
}

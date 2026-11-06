"use client"
import { useState, useCallback, useRef, useEffect } from "react"

interface GeoLocation {
  lng: number
  lat: number
  accuracy: number
}

export type GeolocationError = 'unsupported' | 'denied' | 'unavailable' | 'timeout'

interface UseGeolocationReturn {
  location: GeoLocation | null
  error: GeolocationError | null
  loading: boolean
  requestLocation: () => void
}

export function useGeolocation(): UseGeolocationReturn {
  const [location, setLocation] = useState<GeoLocation | null>(null)
  const [error, setError] = useState<GeolocationError | null>(null)
  const [loading, setLoading] = useState(false)
  const pending = useRef(false)
  const generation = useRef(0)
  useEffect(() => () => { generation.current += 1; pending.current = false }, [])

  const requestLocation = useCallback(() => {
    if (pending.current) return
    setError(null)
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setError('unsupported')
      return
    }
    pending.current = true
    setLoading(true)
    const request = ++generation.current
    const finish = () => {
      if (request !== generation.current) return false
      pending.current = false
      setLoading(false)
      return true
    }
    try {
      navigator.geolocation.getCurrentPosition(
        (pos) => {
          if (!finish()) return
          setLocation({ lng: pos.coords.longitude, lat: pos.coords.latitude, accuracy: Math.round(pos.coords.accuracy) })
          setError(null)
        },
        (err) => {
          if (!finish()) return
          setError(err.code === 1 ? 'denied' : err.code === 3 ? 'timeout' : 'unavailable')
        },
        { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
      )
    } catch {
      if (finish()) setError('unavailable')
    }
  }, [])

  return { location, error, loading, requestLocation }
}

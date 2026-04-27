"use client"
import { useState, useCallback, useEffect } from "react"

interface GeoLocation {
  lng: number
  lat: number
  accuracy: number
}

interface UseGeolocationReturn {
  location: GeoLocation | null
  error: string | null
  refresh: () => void
}

export function useGeolocation(): UseGeolocationReturn {
  const [location, setLocation] = useState<GeoLocation | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetch = useCallback(() => {
    if (typeof navigator === "undefined" || !navigator.geolocation) {
      setError("浏览器不支持定位")
      return
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocation({
          lng: pos.coords.longitude,
          lat: pos.coords.latitude,
          accuracy: Math.round(pos.coords.accuracy),
        })
        setError(null)
      },
      (err) => {
        setError(err.message)
      },
      { enableHighAccuracy: false, timeout: 10000, maximumAge: 300000 },
    )
  }, [])

  useEffect(() => { fetch() }, [fetch])

  return { location, error, refresh: fetch }
}

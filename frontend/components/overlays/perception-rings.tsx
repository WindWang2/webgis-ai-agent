'use client';

interface PerceptionRingsProps {
  active: boolean;
}

export function PerceptionRings({ active }: PerceptionRingsProps) {
  if (!active) return null;

  return (
    <div style={{ position: 'absolute', left: '50%', top: '50%', pointerEvents: 'none', zIndex: 5 }}>
      {[0, 0.8, 1.6].map((delay, idx) => (
        <div
          key={idx}
          style={{
            position: 'absolute',
            borderRadius: '50%',
            border: '1.5px solid rgba(22,163,74,0.5)',
            width: 60 + idx * 40,
            height: 60 + idx * 40,
            top: '50%',
            left: '50%',
            animation: `ringPulse 2.5s ease-out ${delay}s infinite`,
          }}
        />
      ))}
      <div style={{ position: 'absolute', width: 8, height: 8, background: '#16a34a', borderRadius: '50%', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', boxShadow: '0 0 12px rgba(22,163,74,0.7)' }} />
      <style dangerouslySetInnerHTML={{ __html: `
        @keyframes ringPulse {
          0% {
            transform: translate(-50%, -50%) scale(0.5);
            opacity: 0.6;
          }
          100% {
            transform: translate(-50%, -50%) scale(2.5);
            opacity: 0;
          }
        }
      `}} />
    </div>
  );
}

export default PerceptionRings;
export type EvidenceStatus = 'verified' | 'partial' | 'not-verified';

export interface EvidenceItem {
  label: string;
  value: string;
  explanation: string;
}

export const lifecycleEvidence = {
  scope: 'Thử nghiệm chức năng với tải synthetic nhỏ',
  status: 'partial' as EvidenceStatus,
  source: 'docs/evidence/container-lifecycle-recovery-20261114.json',
  stop: {
    model: 'Gemini 3.6 Flash',
    action: 'Dừng mongo-express',
    hostRamAvailable: '9,73%',
  },
  start: {
    model: 'Gemini 3.5 Flash-Lite (fallback)',
    action: 'Bật lại đúng container',
    hostRamAvailable: '11,67%',
  },
  recovery: ['Giới hạn phát tải: 0,2 bản ghi/giây', '3 cửa sổ xử lý sạch', 'Kafka lag cuối: 0'],
};

export const telemetryEvidence: EvidenceItem[] = [
  {
    label: 'Healthy load span',
    value: '7.195,088 giây',
    explanation: 'Phiên được yêu cầu chạy 7.200 giây; đây là tải synthetic, không phải traffic production.',
  },
  {
    label: 'Mẫu telemetry khỏe mạnh',
    value: '1.446',
    explanation: 'Các lần đo có đủ instrumentation trong phiên hai giờ.',
  },
  {
    label: 'Bản ghi được xác nhận',
    value: '219.434',
    explanation: 'Số ACK trong phiên; Kafka lag ở cuối phiên bằng 0.',
  },
];

export const ruleControllerEvidence = [
  { metric: 'Đỉnh hàng đợi Kafka', change: '-74,88%', direction: 'lower' },
  { metric: 'Độ trễ p95', change: '-65,74%', direction: 'lower' },
  { metric: 'Thông lượng cam kết', change: 'Thấp hơn baseline', direction: 'warning' },
  { metric: 'Bản ghi bị từ chối', change: 'Tăng mạnh', direction: 'warning' },
] as const;

export const evidenceFiles = {
  telemetry: 'docs/evidence/telemetry-session-20261114.json',
  controller: 'runtime/research/paired-v4-20260921/analysis/summary.json (artifact local)',
};

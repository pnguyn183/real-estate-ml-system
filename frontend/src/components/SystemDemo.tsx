import {
  Activity,
  ArrowDown,
  ArrowRight,
  Bot,
  CheckCircle2,
  CircleAlert,
  CloudCog,
  Database,
  Gauge,
  LogIn,
  PauseCircle,
  Radio,
  RotateCcw,
  ServerCog,
  ShieldCheck,
  TriangleAlert,
} from 'lucide-react';
import {
  evidenceFiles,
  lifecycleEvidence,
  ruleControllerEvidence,
  telemetryEvidence,
} from '../data/projectEvidence';

interface SystemDemoProps {
  onOpenWorkspace: () => void;
}

const flow = [
  { title: 'Nguồn dữ liệu', description: 'Tin đăng có cấu trúc hoặc văn bản cần trích xuất', icon: Radio },
  { title: 'Kafka', description: 'Nhận và đệm dữ liệu chờ processor xử lý khi tốc độ hai phía lệch nhau', icon: CloudCog },
  { title: 'Processor', description: 'Chuẩn hóa, kiểm tra và lưu kết quả; AI extraction chỉ dùng khi cần', icon: ServerCog },
  { title: 'MongoDB & mô hình', description: 'Lưu dữ liệu hợp lệ, huấn luyện riêng và phục vụ dự đoán', icon: Database },
];

const signals = [
  { name: 'Lưu lượng', value: 'bản ghi/giây', description: 'Nhu cầu gửi vào và lượng hệ thống thực sự nhận.', icon: Activity },
  { name: 'Độ trễ đuôi', value: 'p95', description: '95% tác vụ hoàn thành không chậm hơn giá trị này.', icon: Gauge },
  { name: 'Lỗi', value: 'tỷ lệ lỗi', description: 'Bao nhiêu tác vụ thất bại, retry hoặc bị từ chối.', icon: CircleAlert },
  { name: 'Bão hòa', value: 'lag + CPU/RAM', description: 'Hàng đợi và tài nguyên cho biết hệ thống đang “đầy” tới đâu.', icon: TriangleAlert },
];

export default function SystemDemo({ onOpenWorkspace }: SystemDemoProps) {
  return (
    <main className="bg-slate-950 text-slate-100">
      <section className="relative overflow-hidden border-b border-white/10">
        <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_right,_rgba(8,145,178,0.22),_transparent_45%)]" />
        <div className="relative mx-auto grid max-w-7xl gap-10 px-4 py-16 lg:grid-cols-[1.15fr_0.85fr] lg:items-center lg:py-24">
          <div>
            <p className="mb-4 inline-flex rounded-full border border-cyan-400/30 bg-cyan-400/10 px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-200">
              Demo vận hành có bằng chứng
            </p>
            <h2 className="max-w-3xl text-4xl font-semibold leading-tight sm:text-5xl">
              Biến luồng tin đăng thành dữ liệu có thể xử lý, quan sát và điều tiết.
            </h2>
            <p className="mt-5 max-w-2xl text-base leading-7 text-slate-300 sm:text-lg">
              Hệ thống tách nhận dữ liệu, xử lý, trích xuất AI, huấn luyện và giám sát. Khi tải hoặc tài nguyên có dấu hiệu bất thường, controller giảm tải; LLM có thể đề xuất hành động trong phạm vi an toàn.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <a href="#system-flow" className="rounded bg-cyan-400 px-5 py-3 text-sm font-semibold text-slate-950 hover:bg-cyan-300">
                Xem luồng hệ thống
              </a>
              <button
                type="button"
                onClick={onOpenWorkspace}
                className="inline-flex items-center gap-2 rounded border border-white/20 px-5 py-3 text-sm font-semibold hover:bg-white/10"
              >
                <LogIn className="h-4 w-4" /> Mở khu vực dự đoán
              </button>
            </div>
          </div>
          <div className="rounded-2xl border border-white/10 bg-white/5 p-6 backdrop-blur">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-slate-300">Trạng thái bằng chứng</span>
              <span className="inline-flex items-center gap-1 rounded-full bg-amber-400/10 px-3 py-1 text-xs font-semibold text-amber-200">
                <ShieldCheck className="h-3.5 w-3.5" /> Có giới hạn
              </span>
            </div>
            <div className="mt-6 space-y-4">
              <StatusRow label="Telemetry hai giờ" status="Đã kiểm chứng" tone="success" />
              <StatusRow label="LLM dừng và bật container" status="Đã kiểm chứng chức năng" tone="success" />
              <StatusRow label="LLM ổn định hệ thống khi quá tải" status="Chưa đủ bằng chứng" tone="warning" />
              <StatusRow label="Forecast tham gia controller" status="Chưa tích hợp" tone="neutral" />
            </div>
          </div>
        </div>
      </section>

      <section id="system-flow" className="mx-auto max-w-7xl px-4 py-16">
        <SectionTitle eyebrow="Từ đầu vào đến kết quả" title="Dữ liệu đi qua hệ thống như thế nào?" />
        <div className="mt-10 grid gap-4 lg:grid-cols-4">
          {flow.map(({ title, description, icon: Icon }, index) => (
            <div key={title} className="relative rounded-xl border border-white/10 bg-white/[0.04] p-5">
              <div className="flex items-start justify-between">
                <span className="grid h-10 w-10 place-items-center rounded-lg bg-cyan-400/10 text-cyan-300"><Icon className="h-5 w-5" /></span>
                <span className="text-xs font-semibold text-slate-500">0{index + 1}</span>
              </div>
              <h3 className="mt-5 font-semibold">{title}</h3>
              <p className="mt-2 text-sm leading-6 text-slate-400">{description}</p>
              {index < flow.length - 1 && <ArrowRight className="absolute -right-3 top-1/2 z-10 hidden h-5 w-5 text-cyan-500 lg:block" />}
            </div>
          ))}
        </div>
        <p className="mt-5 rounded-lg border border-cyan-400/20 bg-cyan-400/5 p-4 text-sm leading-6 text-slate-300">
          Giá trị thực tế: Kafka hấp thụ chênh lệch tốc độ; processor giữ quy tắc dữ liệu ổn định; extraction hỗ trợ văn bản khó; monitoring cho biết lúc nào cần giảm tải. Forecast hiện mới là thử nghiệm offline, chưa điều khiển hệ thống.
        </p>
      </section>

      <section className="border-y border-white/10 bg-white/[0.025]">
        <div className="mx-auto max-w-7xl px-4 py-16">
          <SectionTitle eyebrow="Bốn tín hiệu vận hành" title="Hệ thống nhận biết tải tăng bằng gì?" />
          <div className="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {signals.map(({ name, value, description, icon: Icon }) => (
              <article key={name} className="rounded-xl border border-white/10 bg-slate-950/60 p-5">
                <Icon className="h-5 w-5 text-cyan-300" />
                <p className="mt-4 text-xs font-semibold uppercase tracking-widest text-cyan-300">{value}</p>
                <h3 className="mt-1 text-lg font-semibold">{name}</h3>
                <p className="mt-2 text-sm leading-6 text-slate-400">{description}</p>
              </article>
            ))}
          </div>
          <p className="mt-5 text-sm text-slate-400">CPU/RAM phải ghi rõ phạm vi host, VM hay container; các phạm vi này không thể thay thế lẫn nhau.</p>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-16">
        <SectionTitle eyebrow="Một vòng điều khiển đã chạy thật" title="Agent đã quan sát và hành động ra sao?" />
        <p className="mt-4 max-w-3xl text-sm leading-6 text-slate-400">
          Đây là thử nghiệm chức năng bằng tải synthetic nhỏ. Hai quyết định đều do LLM trả về, còn safety guard kiểm tra quyền, dependency và điều kiện RAM trước khi thi hành.
        </p>
        <div className="mt-10 grid gap-4 lg:grid-cols-[1fr_auto_1fr_auto_1fr] lg:items-stretch">
          <TimelineCard icon={Bot} step="Quan sát" title="RAM khả dụng 9,73%" body="Gemini 3.6 Flash chọn dừng mongo-express, một dịch vụ không thiết yếu cho pipeline." />
          <ArrowDown className="mx-auto h-5 w-5 text-slate-600 lg:hidden" />
          <ArrowRight className="m-auto hidden h-5 w-5 text-slate-600 lg:block" />
          <TimelineCard icon={PauseCircle} step="Bảo vệ" title="Guard chờ điều kiện an toàn" body="Không phát tải mới; kiểm tra lease, dependency và ngưỡng RAM. Guard không tự chọn hành động thay LLM." />
          <ArrowDown className="mx-auto h-5 w-5 text-slate-600 lg:hidden" />
          <ArrowRight className="m-auto hidden h-5 w-5 text-slate-600 lg:block" />
          <TimelineCard icon={RotateCcw} step="Khôi phục" title="RAM khả dụng 11,67%" body="Gemini fallback chọn bật lại đúng container; hệ thống tiếp tục ở 0,2 bản ghi/giây và kết thúc với lag 0." />
        </div>
        <div className="mt-6 rounded-xl border border-amber-400/20 bg-amber-400/5 p-5 text-sm leading-6 text-amber-100">
          <strong>Điều chưa được chứng minh:</strong> việc dừng container có gây ra mức tăng RAM hay không, và LLM có giữ hệ thống ổn định trong một lượt quá tải tương đương baseline hay không. Vì vậy chưa có phần trăm cải thiện của LLM.
        </div>
        <EvidenceSource path={lifecycleEvidence.source} />
      </section>

      <section className="border-y border-white/10 bg-white/[0.025]">
        <div className="mx-auto grid max-w-7xl gap-10 px-4 py-16 lg:grid-cols-2">
          <div>
            <SectionTitle eyebrow="Quan sát liên tục" title="Telemetry đã thu được gì?" />
            <div className="mt-8 grid gap-3 sm:grid-cols-3 lg:grid-cols-1 xl:grid-cols-3">
              {telemetryEvidence.map((item) => (
                <div key={item.label} className="rounded-xl border border-white/10 bg-slate-950/60 p-4">
                  <p className="text-2xl font-semibold text-cyan-200">{item.value}</p>
                  <p className="mt-1 text-sm font-medium">{item.label}</p>
                  <p className="mt-2 text-xs leading-5 text-slate-400">{item.explanation}</p>
                </div>
              ))}
            </div>
            <EvidenceSource path={evidenceFiles.telemetry} />
          </div>
          <div>
            <SectionTitle eyebrow="Đối chứng hiện có" title="Giảm lag chưa đồng nghĩa tăng hiệu năng" />
            <div className="mt-8 space-y-3">
              {ruleControllerEvidence.map((row) => (
                <div key={row.metric} className="flex items-center justify-between gap-4 rounded-lg border border-white/10 bg-slate-950/60 px-4 py-3">
                  <span className="text-sm text-slate-300">{row.metric}</span>
                  <span className={row.direction === 'lower' ? 'font-semibold text-emerald-300' : 'font-semibold text-amber-200'}>{row.change}</span>
                </div>
              ))}
            </div>
            <p className="mt-4 text-sm leading-6 text-slate-400">
              Đây là kết quả của controller theo luật, không phải LLM. Lượng nhận vào, lượng xử lý xong và lượng bị hoãn/từ chối phải được đọc cùng nhau.
            </p>
            <EvidenceSource path={evidenceFiles.controller} />
          </div>
        </div>
      </section>

      <section className="mx-auto max-w-7xl px-4 py-16">
        <SectionTitle eyebrow="Kết luận trung thực" title="Hệ thống đã giải quyết được gì — và chưa giải quyết được gì?" />
        <div className="mt-10 grid gap-5 lg:grid-cols-2">
          <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/5 p-6">
            <h3 className="flex items-center gap-2 font-semibold text-emerald-200"><CheckCircle2 className="h-5 w-5" /> Đã có bằng chứng</h3>
            <ul className="mt-4 space-y-3 text-sm leading-6 text-slate-300">
              <li>• Luồng Kafka → processor → MongoDB hoạt động và có telemetry.</li>
              <li>• Controller theo luật có thể giảm tải và drain hàng đợi.</li>
              <li>• LLM đã dừng/bật lại container trong một thử nghiệm chức năng có guard.</li>
              <li>• Forecast 5 và 10 phút vượt persistence trên một phiên synthetic hai giờ.</li>
            </ul>
          </div>
          <div className="rounded-xl border border-amber-400/20 bg-amber-400/5 p-6">
            <h3 className="flex items-center gap-2 font-semibold text-amber-200"><TriangleAlert className="h-5 w-5" /> Chưa thể kết luận</h3>
            <ul className="mt-4 space-y-3 text-sm leading-6 text-slate-300">
              <li>• Chưa có A/B hợp lệ chứng minh LLM cải thiện khi quá tải.</li>
              <li>• Forecast chưa tham gia quyết định controller.</li>
              <li>• Extraction Gemini thật mới có một fixture, chưa đại diện cho độ chính xác dataset.</li>
              <li>• Benchmark giá đã chặn price-text leakage, exact duplicate và group overlap; vẫn chưa chứng minh tổng quát hóa ngoài snapshot.</li>
            </ul>
          </div>
        </div>
      </section>
    </main>
  );
}

function SectionTitle({ eyebrow, title }: { eyebrow: string; title: string }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">{eyebrow}</p>
      <h2 className="mt-2 max-w-3xl text-3xl font-semibold">{title}</h2>
    </div>
  );
}

function TimelineCard({ icon: Icon, step, title, body }: { icon: typeof Bot; step: string; title: string; body: string }) {
  return (
    <article className="rounded-xl border border-white/10 bg-white/[0.04] p-5">
      <Icon className="h-5 w-5 text-cyan-300" />
      <p className="mt-4 text-xs font-semibold uppercase tracking-widest text-cyan-300">{step}</p>
      <h3 className="mt-1 font-semibold">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-slate-400">{body}</p>
    </article>
  );
}

function StatusRow({ label, status, tone }: { label: string; status: string; tone: 'success' | 'warning' | 'neutral' }) {
  const toneClass = tone === 'success' ? 'text-emerald-300' : tone === 'warning' ? 'text-amber-200' : 'text-slate-400';
  return (
    <div className="flex items-center justify-between gap-4 border-b border-white/10 pb-3 text-sm last:border-0 last:pb-0">
      <span className="text-slate-300">{label}</span>
      <span className={`text-right font-semibold ${toneClass}`}>{status}</span>
    </div>
  );
}

function EvidenceSource({ path }: { path: string }) {
  return <p className="mt-4 break-all font-mono text-[11px] leading-5 text-slate-500">Nguồn: {path}</p>;
}

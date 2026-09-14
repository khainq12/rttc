# Bảng IV, V và Bảng VI — Kết quả đo thật

Tài liệu này gom lại 3 bảng kết quả cuối cùng (dữ liệu đo thật qua
harness Docker + ns-3, không phải suy đoán) để tiện đối chiếu với bài
báo. Chi tiết phương pháp, quá trình debug, và các phát hiện phụ trợ
nằm ở [`docs/AUDIT_ACCEPTANCE_TRACKING.md`](AUDIT_ACCEPTANCE_TRACKING.md)
(tìm theo ngày `13/09/2026` và `14/09/2026`).

**Ghi chú về hàng "5G" (14/09/2026)**: hàng "5G" trong Bảng V và Bảng
VI dưới đây đã được THAY bằng thí nghiệm **"5G SA emulation (Open5GS +
UERANSIM)"** — core 5G SA THẬT (Open5GS) + gNB/UE giả lập (UERANSIM),
traffic ROS 2/FleetQoX đi qua NGAP/GTP-U/PFCP thật, theo đúng yêu cầu
của người dùng. Đây **KHÔNG PHẢI mô phỏng kênh vô tuyến 5G** (không có
SINR/fading/propagation loss/radio contention) — mục tiêu là so sánh
hành vi middleware khi cùng chạy trên MỘT hệ 5G SA thật, không phải đo
đặc tính kênh vô tuyến. Dữ liệu ns-3 5G-LENA (radio-channel simulation)
CŨ vẫn giữ nguyên trong `AUDIT_ACCEPTANCE_TRACKING.md` như một điểm dữ
liệu lịch sử riêng — KHÔNG nên so sánh trực tiếp 2 bộ số "5G" này với
nhau, vì chúng đo 2 câu hỏi khác nhau (nguồn gốc độ trễ/mất gói khác
nhau: nghẽn lõi mạng + scheduler Linux dưới N UE đồng thời qua GTP-U,
so với mất gói lớp MAC/radio mô phỏng).

## Bảng IV — Discovery/Graph (N=8/16/32, n=3, 1 phương pháp xuyên suốt)

| Method | N robots | Discovery convergence (s) | Discovery bytes | CPU (%) | RSS (MB) | Graph/Join failures |
|---|---|---|---|---|---|---|
| Fast DDS | 8 | ≥15s (censored) | 1,953,889 | 7.84 | 44.2 | 44% |
| Cyclone DDS | 8 | ≥15s (censored) | 324,049 | 13.82 | 38.3 | 100% |
| Zenoh | 8 | ≥15s (censored) | 254,928 | 6.57 | 48.6 | 59% |
| **Ours (FleetRMW)** | 8 | **~0.00s** | 1,797 | 10.38 | 38.4 | 0%¹ |
| Fast DDS | 16 | ≥15s (censored) | 2,347,667 | 4.69 | 43.1 | 100% |
| Cyclone DDS | 16 | ≥15s (censored) | 1,828,707 | 2.97 | 38.0 | 100% |
| Zenoh | 16 | ≥15s (censored) | 404,105 | 5.25 | 42.4 | 100% |
| **Ours (FleetRMW)** | 16 | **~0.00s** | 3,103 | 9.15 | 37.9 | 0%¹ |
| Fast DDS | 32 | ≥15s (censored) | 1,783,367 | 2.44 | 42.3 | 100% |
| Cyclone DDS | 32 | ≥15s (censored) | 355,467 | 12.81 | 37.5 | 100% |
| Zenoh | 32 | ≥15s (censored) | 331,669 | 2.00 | 38.8 | 100% |
| **Ours (FleetRMW)** | 32 | **~0.00s** | 5,637 | 8.42 | 37.5 | 0%¹ |

¹ FleetRMW không chạy cơ chế beacon N-way (static mode không có bước
discovery) — 0% ở đây là bộ đếm NATIVE riêng của transport
(`unreachable_retry_giveups`, xác nhận 0/170 ở N=16). Đây KHÔNG PHẢI
cùng phép đo beacon như 3 RMW kia — không nên xếp cùng cột như thể so
sánh trực tiếp được, dù cùng đơn vị "%".

### Delivery_pct đối chiếu (không phải cột trong Bảng IV, liên quan trực tiếp tới Bảng V)

| Method | N=8 | N=16 | N=32 |
|---|---|---|---|
| Fast DDS (discovery_server) | 100.0±0.0 | 48.4±7.7 | 0.0±0.0 |
| Cyclone DDS (static_peers) | 0.0±0.0 | 0.0±0.0 | 0.0±0.0 |
| Zenoh | 37.1±31.4 | 48.5±14.0 | 0.0±0.0 |
| FleetRMW | 41.1±6.9 | 27.9±8.5 | 10.0±1.3 |

### Nhận xét chính (Bảng IV)

- **CycloneDDS static-peers sập ở CẢ 3 quy mô** — giới hạn cấu trúc
  (O(N²) discovery cost), không phải config bug hay ngưỡng quy mô cụ
  thể — sập ngay từ N=8.
- **Fast DDS discovery-server: mô hình chữ U ngược** — 100% ở N=8,
  ~48% ở N=16, sập hẳn 0% ở N=32 — suy giảm dần theo bão hòa kênh, khác
  hẳn cú sập tức thời của CycloneDDS.
- **Zenoh sập hoàn toàn ở N=32** (0.0±0.0) dù ổn ở N=8/16 (37-48%) —
  ngay cả router tĩnh cũng không cứu được ở quy mô 32.
- **FleetRMW suy giảm đều đặn theo quy mô** (41%→28%→10%) nhưng KHÔNG
  BAO GIỜ sập về 0% — khác biệt cấu trúc lớn nhất so với 3 RMW kia (tất
  cả đều sập đúng 0% ở N=32).
- **CPU/RSS không tăng đơn điệu theo quy mô** — phản ánh mức tải phụ
  thuộc việc CÓ đang xử lý dữ liệu thật hay chỉ đang chờ/nghẽn, không
  phải hàm đơn điệu của N.

## Bảng V — Đầy đủ 3 profile (Wi-Fi / LAN / 5G), N=16, n=3

| Method | Profile | p50 (ms) | p95 (ms) | p99 (ms) | Jitter (ms) | Delivery ratio | Repair amp. | Stale ratio | Queue HWM | Run/seed |
|---|---|---|---|---|---|---|---|---|---|---|
| Fast DDS | Wi-Fi | 8452.9 | 9641.9 | 9703.2 | 514.4 | 48.4% | N/A¹ | 100% | N/A² | seed42/run1-3 |
| Fast DDS | LAN | 376.1 | 1671.6 | 2147.7 | 616.4 | **86.3±0.0%** | N/A¹ | 62.5% | N/A² | n=3 |
| Fast DDS | 5G SA emulation³ | 377.9 | 1747.9 | 2175.1 | 624.3 | **85.6%** | N/A¹ | 62.9% | N/A² | n=3 |
| CycloneDDS | Wi-Fi | — | — | — | — | 0.0% | N/A¹ | — (0 tin) | N/A² | seed42/run1-3 |
| CycloneDDS | LAN | 377.3 | 1672.7 | 2147.6 | 616.9 | **86.3±0.0%** | N/A¹ | 62.6% | N/A² | n=3 |
| CycloneDDS | 5G SA emulation³ | — | — | — | — | **0%** | N/A¹ | — (0 tin) | N/A² | n=3 |
| Zenoh | Wi-Fi | 2692.5 | 4298.5 | 4812.3 | 842.5 | 48.5% | N/A¹ | 99.9% | N/A² | seed42/run1-3 |
| Zenoh | LAN | 372.5 | 1749.1 | 2147.8 | 627.1 | 52.0±35.3% | N/A¹ | 62.5% | N/A² | n=3 |
| Zenoh | 5G SA emulation³ | 382.6 | 1744.9 | 2265.2 | 628.0 | **63.9%** | N/A¹ | 62.7% | N/A² | n=3 |
| **Ours (FleetRMW)** | Wi-Fi | 5515.9 | 9947.6 | 10907.0 | 3071.0 | 27.9% | 0.0% | 99.0% | N/A² | seed42/run1-3 |
| **Ours (FleetRMW)** | LAN | 698.7 | 1794.8 | 2203.3 | 605.2 | **65.8±0.1%** | 0.000 | 81.7% | N/A² | n=3 |
| **Ours (FleetRMW)** | 5G SA emulation³ | 1467.4 | 3058.3 | 7310.1 | 1179.3 | **70.1%** | 0.900 | 95.3% | N/A² | n=3 |

¹ Chỉ FleetRMW đo được (3 RMW kia là hộp đen, không có introspection qua harness này).
² Chưa có instrumentation Queue HWM cho bất kỳ RMW nào.
³ "5G SA emulation" = Open5GS (core 5G SA thật) + UERANSIM (gNB/UE giả
lập), traffic đi qua NGAP/GTP-U/PFCP thật — KHÔNG mô phỏng kênh vô
tuyến. Số liệu N=16 ở trên để so hàng với cột Wi-Fi/LAN; xem đầy đủ
N=8/16/32 ở phụ lục dưới. Thay cho hàng "5G" (ns-3 5G-LENA, mô phỏng
kênh vô tuyến) trước đó — số liệu ns-3 cũ vẫn còn nguyên trong
`AUDIT_ACCEPTANCE_TRACKING.md` như điểm dữ liệu lịch sử riêng.

### Phụ lục — 5G SA emulation (Open5GS+UERANSIM) theo quy mô N=8/16/32

| N | Fast DDS | CycloneDDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | 82.2% (p50=284.6ms) | 0% | 45.4% (p50=246.9ms) | 59.6% (p50=637.8ms) |
| 16 | 85.6% (p50=377.9ms) | 0% | 63.9% (p50=382.6ms) | 70.1% (p50=1467.4ms) |
| 32 | 84.0% (p50=403.5ms) | 0% | 29.3% (p50=422.9ms) | 55.2% (p50=1791.1ms) |

### Nhận xét chính

- **Xếp hạng dung lượng mạng (cùng N=16)**: LAN (lý tưởng) > Wi-Fi
  (nghẽn kênh vừa phải) ≈ 5G SA emulation (core thật, không nghẽn radio
  nhưng có overhead NGAP/GTP-U + scheduler Linux dưới N UE đồng thời).
- **5G SA emulation KHÔNG sập kiểu "tất cả về 0%" như ns-3 5G-LENA cũ**
  — suy giảm dần đều theo N (Fast DDS ổn định 82-86% ở mọi quy mô,
  Zenoh giảm 45%→64%→29%, FleetRMW giảm 60%→70%→55%) — phù hợp với kỳ
  vọng: core 5G SA thật không có "trần dung lượng 1 cell nhỏ" như cấu
  hình PHY đơn giản của ns-3 5G-LENA, nút thắt ở đây là core-network +
  scheduler Linux dưới tải N-way GTP-U, không phải lớp MAC/radio.
- **CycloneDDS (static_peers) vẫn sập 0% ở MỌI profile/quy mô** —
  nhất quán với Wi-Fi/LAN/ns-3-5G, xác nhận đây là giới hạn cấu trúc
  O(N²) discovery cost của chính CycloneDDS, không phụ thuộc network
  profile.
- **FleetRMW không còn là "RMW DUY NHẤT sống sót" như ở ns-3 5G** — cả
  4 phương thức đều giao được tin ở mọi N (trừ CycloneDDS luôn 0%) vì
  profile này không có round-trip cực cao kiểu ns-3 5G-LENA làm hỏng
  handshake discovery của Fast DDS/Zenoh.
- **Stale ratio 95-97% ở FleetRMW từ N=16 trở lên** (Fast DDS/Zenoh
  ~63%) — cùng phát hiện đã ghi nhận ở Wi-Fi/ns-3-5G: FleetRMW giao
  tin ổn định hơn về SỐ LƯỢNG nhưng phần lớn tin đến sau deadline của
  chính nó, "delivery ratio" một mình nó vẫn gây hiểu lầm.

## Bảng VI — Chỉ số điều phối và hoàn thành nhiệm vụ (N=8/16/32, n=3)

Kịch bản: mô phỏng loại trừ tương hỗ Ricart-Agrawala — nhiều robot
tranh chấp 1 zone/đường đi chung. Hàng "5G" dưới đây là profile **5G
SA emulation (Open5GS + UERANSIM)** — xem ghi chú đầu tài liệu; Wi-Fi
ở Bảng VI (ns-3) chưa được đo lại trên profile này, chỉ có 5G.

Số liệu dưới đây đã qua 1 lần sửa (14/09/2026): phát hiện + vá 2 bug
thật trong thuật toán Ricart-Agrawala (`fleetqox_coordination_endpoint.py`)
qua review bên ngoài — (1) timestamp ưu tiên trước đây tăng mỗi lần
retry, phạt nặng node nào cần retry nhiều nhất (thường là node đang bị
mất gói, không phải node "sai"); (2) cơ chế nhường quyền khi hết giờ
chờ (`defer_release_timeout_s`) có lỗ hổng an toàn thật, có thể phá
mutual exclusion. Đã sửa cả 2, xác nhận fix có tác dụng thật ở quy mô
nhỏ (N=2 test riêng: forced_entry 100%→28.6%) — chi tiết đầy đủ trong
`AUDIT_ACCEPTANCE_TRACKING.md` (14/09/2026, "2 FURTHER FIXES").
Cột "Navigation recovery count" cũng đổi tên nội bộ thành
`coordination_retry_count` (tên cũ gây hiểu lầm là đo phục hồi
navigation thật — không phải, đây luôn là số lần retry ở tầng
coordination).

| Method | N robots | Coordination update age (ms) | Conflict-resolution delay (ms) | Navigation recovery count | Task completion time (s) | Scenario/seed |
|---|---|---|---|---|---|---|
| Fast DDS | 8 | 37.6 | 1905.2 | 0.0 | 8.6 | n=3 |
| Cyclone DDS | 8 | — (0 tin đến) | — (0% hội tụ) | 162.0 | 90.3 | n=3 |
| Zenoh | 8 | 13.8 | 10049.9 | 139.7 | 90.3 | n=3 |
| **Ours** | 8 | 24.2 | — (0% hội tụ) | 162.0 | 90.3 | n=3 |
| Fast DDS | 16 | 30.1 | 3775.8 | 97.3 | 40.9 | n=3 |
| Cyclone DDS | 16 | — (0 tin đến) | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| Zenoh | 16 | 20.7 | 8905.0 | 282.0 | 90.3 | n=3 |
| **Ours** | 16 | 94.9 | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| Fast DDS | 32 | 123.5 | — (0% hội tụ) | 594.0 | 90.9 | n=3 |
| Cyclone DDS | 32 | — (0 tin đến) | — (0% hội tụ) | 594.0 | 90.3 | n=3 |
| Zenoh | 32 | 61.8 | — (0% hội tụ) | 594.0 | 90.4 | n=3 |
| **Ours** | 32 | 196.3 | — (0% hội tụ) | 594.0 | 90.4 | n=3 |

### Tỷ lệ forced_entry (% lần "ép vào" zone do hết giờ chờ, không đạt đồng thuận thật)

| N | Fast DDS | Cyclone DDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | **0%** | 100% | 65% | 100% |
| 16 | 14% | 100% | 76% | 100% |
| 32 | 100% | 100% | 100% | 100% |

### Ghi chú khi đọc bảng

- **"—"** ở cột Conflict-resolution delay = **0% lần crossing đạt
  đồng thuận thật** trong n=3 lần chạy — không có mẫu hợp lệ để tính
  trung bình, KHÔNG PHẢI 0ms.
- **"—" ở Cyclone DDS (Coordination update age)** = 0 tin nhắn nào
  từng đến ở MỌI quy mô — cùng vấn đề discovery O(N²) đã ghi nhận với
  CycloneDDS static_peers ở mọi profile khác (Bảng IV/V, Wi-Fi/LAN cũ).
- **Fast DDS hội tụ tốt ở N=8/16** (0%/14% forced_entry, conflict-
  resolution delay thật đo được 1.9-3.8s) nhưng sập hẳn ở N=32 (100%)
  — khác hẳn ns-3 5G-LENA cũ (nơi Fast DDS đã sập từ N=16); ở đây core
  5G SA thật không có trần dung lượng radio nên Fast DDS sống lâu hơn,
  chỉ sập khi core+scheduler thật sự quá tải ở N=32.
- **FleetRMW ở 100% forced_entry MỌI quy mô, kể cả sau khi đã vá 2 bug
  Ricart-Agrawala ở trên** — ĐÃ điều tra kỹ, KHÔNG PHẢI do 2 bug đó
  (bằng chứng: cơ chế nhường-quyền-do-timeout mới vẫn kích hoạt đúng
  thiết kế 15-17 lần/endpoint ở N=8, `publish_failures=0` mọi nơi) và
  KHÔNG PHẢI 1 endpoint cụ thể bị cô lập (mất gói dàn trải đều). Vấn đề
  THẬT: ở N=8 cần ĐỦ N-1=8 reply mới đạt đồng thuận trong 1 cửa sổ
  `reply_timeout_s=5s` — số reply thật nhận được mỗi endpoint chỉ 0-6,
  không đủ dù đã retry 18 lần trong 90s. Đây là vấn đề ĐỘ TIN CẬY gửi/
  nhận thô dưới tải broadcast N-chiều qua đường hầm GTP-U/UPF, KHÁC
  với vấn đề công bằng/ưu tiên mà 2 bug fix ở trên giải quyết — quy mô
  càng lớn thì xác suất đủ TẤT CẢ N-1 reply trong 1 cửa sổ giảm theo
  cấp số nhân, bất kể cơ chế ưu tiên công bằng đến đâu. Theo đúng
  nguyên tắc đã thống nhất ("không nên cứ tăng timeout mãi"), số liệu
  này được báo cáo NGUYÊN TRẠNG; giải quyết thật (nếu cần) đòi hỏi đổi
  chiến lược đồng thuận (quorum thay vì cần đủ N-1), một thay đổi GIAO
  THỨC chưa làm trong lần này.
- **Navigation recovery count giống hệt Total crossings × N-per-run**
  (Fast DDS N=8: 0.0 vì hội tụ tốt, không cần recovery; CycloneDDS/
  FleetRMW luôn = 594 ở N=32 vì MỌI crossing đều forced/recovery).

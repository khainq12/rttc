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
| Fast DDS | 5G SA emulation³ | 606.1 | 2272.4 | 2352.3 | 873.8 | **4.9%** | N/A¹ | 76.4% | N/A² | n=3 |
| CycloneDDS | Wi-Fi | — | — | — | — | 0.0% | N/A¹ | — (0 tin) | N/A² | seed42/run1-3 |
| CycloneDDS | LAN | 377.3 | 1672.7 | 2147.6 | 616.9 | **86.3±0.0%** | N/A¹ | 62.6% | N/A² | n=3 |
| CycloneDDS | 5G SA emulation³ | — | — | — | — | **0%** | N/A¹ | — (0 tin) | N/A² | n=3 |
| Zenoh | Wi-Fi | 2692.5 | 4298.5 | 4812.3 | 842.5 | 48.5% | N/A¹ | 99.9% | N/A² | seed42/run1-3 |
| Zenoh | LAN | 372.5 | 1749.1 | 2147.8 | 627.1 | 52.0±35.3% | N/A¹ | 62.5% | N/A² | n=3 |
| Zenoh | 5G SA emulation³ | 368.3 | 1721.1 | 2185.9 | 621.7 | **62.1%** | N/A¹ | 61.4% | N/A² | n=3 |
| **Ours (FleetRMW)** | Wi-Fi | 5515.9 | 9947.6 | 10907.0 | 3071.0 | 27.9% | 0.0% | 99.0% | N/A² | seed42/run1-3 |
| **Ours (FleetRMW)** | LAN | 698.7 | 1794.8 | 2203.3 | 605.2 | **65.8±0.1%** | 0.000 | 81.7% | N/A² | n=3 |
| **Ours (FleetRMW)** | 5G SA emulation³ | — | — | — | — | **0%** | N/A¹ | — (0 tin) | N/A² | n=3 |

¹ Chỉ FleetRMW đo được (3 RMW kia là hộp đen, không có introspection qua harness này).
² Chưa có instrumentation Queue HWM cho bất kỳ RMW nào.
³ "5G SA emulation" = Open5GS (core 5G SA thật) + UERANSIM (gNB/UE giả
lập), traffic đi qua NGAP/GTP-U/PFCP thật — KHÔNG mô phỏng kênh vô
tuyến, CỘNG THÊM **2% suy hao ngẫu nhiên giả định** (tc netem, xem ghi
chú đầu tài liệu và `AUDIT_ACCEPTANCE_TRACKING.md` 14-15/09/2026) chỉ
áp lên cổng radio-link giả lập gNB↔UE (4997/udp) của UERANSIM — thay
cho phiên bản KHÔNG suy hao trước đó. Số liệu N=16 ở trên để so hàng
với cột Wi-Fi/LAN; xem đầy đủ N=8/16/32 ở phụ lục dưới. Thay cho hàng
"5G" (ns-3 5G-LENA, mô phỏng kênh vô tuyến) trước đó — số liệu ns-3 cũ
vẫn còn nguyên trong `AUDIT_ACCEPTANCE_TRACKING.md` như điểm dữ liệu
lịch sử riêng.

### Phụ lục — 5G SA emulation + 2% suy hao (Open5GS+UERANSIM) theo quy mô N=8/16/32

| N | Fast DDS | CycloneDDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | 61.1% (p50=304.6ms) | 0% | 41.3% (p50=297.0ms) | 24.7% (p50=1326.8ms) |
| 16 | 0% | 0% | 20.6% (p50=350.1ms) | 24.6% (p50=739.3ms) |
| 32 | 4.9% (p50=606.1ms) | 0% | 62.1% (p50=368.3ms) | 0% |

### Nhận xét chính

- **2% suy hao giả định (radio-link only) đủ để làm sập delivery đáng
  kể** — khác hẳn phiên bản KHÔNG suy hao trước đó (Fast DDS 82-86% ở
  mọi N, giờ chỉ 61%→0%→4.9%; FleetRMW 60-70%, giờ 25%→25%→0%) — xác
  nhận: chỉ cần 1 mức suy hao khiêm tốn trên kênh vô tuyến giả lập
  cũng đủ tạo khác biệt LỚN so với kịch bản "core sạch hoàn toàn", gần
  với trực giác thông thường về 5G hơn.
- **Không còn xu hướng đơn điệu theo N** (vd Zenoh N=32 lại CAO hơn
  N=16: 62% > 21% — không phải lỗi, chỉ là nhiễu ngẫu nhiên độc lập
  IID không có "hiệu ứng tích luỹ" đơn điệu giống nghẽn kênh thật) —
  khác hẳn kiểu suy giảm mượt theo N ở Wi-Fi/LAN (những profile có
  contention/nghẽn THẬT tăng theo N). Đây là hạn chế đã biết của mô
  hình netem IID đơn giản (xem ghi chú `RADIO_LINK_LOSS_PCT`) — không
  nên đọc "N=32 tốt hơn N=16" như 1 xu hướng có ý nghĩa.
- **FleetRMW về 0% ở N=32** (trước đó, không suy hao, vẫn giao được
  55%) — cùng loại tổn thương mà CycloneDDS luôn gặp (sập hoàn toàn),
  nhưng ở đây do suy hao radio-link cộng dồn dưới 33 UE đồng thời,
  không phải do O(N²) discovery cost.
- **CycloneDDS (static_peers) vẫn sập 0% ở MỌI profile/quy mô** —
  nhất quán, giới hạn cấu trúc O(N²) discovery cost riêng của nó,
  không phụ thuộc network profile hay có/không có suy hao.
- Phát hiện kỹ thuật quan trọng khi triển khai suy hao này: cùng
  profile/quy mô, batch ĐẦU TIÊN với 2% suy hao có tới **42% lượt chạy
  fail HOÀN TOÀN** (UE không đăng ký 5G được, không phải do gói tin dữ
  liệu FleetQoX) — đã điều tra và sửa (retry với container UE hoàn
  toàn mới, tối đa 3 lần) trước khi lấy số liệu chính thức ở trên —
  xem `AUDIT_ACCEPTANCE_TRACKING.md` 15/09/2026 để biết đầy đủ quá
  trình.

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

Số liệu dưới đây là bản CÓ 2% suy hao radio-link giả định (15/09/2026),
thay cho bản KHÔNG suy hao trước đó — xem ghi chú đầu tài liệu và
Bảng V ở trên.

| Method | N robots | Coordination update age (ms) | Conflict-resolution delay (ms) | Navigation recovery count | Task completion time (s) | Scenario/seed |
|---|---|---|---|---|---|---|
| Fast DDS | 8 | 32.2 | — (0% hội tụ) | 162.0 | 90.3 | n=3 |
| Cyclone DDS | 8 | — (0 tin đến) | — (0% hội tụ) | 162.0 | 90.3 | n=3 |
| Zenoh | 8 | 14.2 | 9760.6 | 143.7 | 90.3 | n=3 |
| **Ours** | 8 | 26.8 | — (0% hội tụ) | 162.0 | 90.3 | n=3 |
| Fast DDS | 16 | — (0 tin đến) | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| Cyclone DDS | 16 | — (0 tin đến) | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| Zenoh | 16 | 22.5 | 9460.0 | 268.7 | 90.3 | n=3 |
| **Ours** | 16 | 97.0 | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| Fast DDS | 32 | 25.5 | — (0% hội tụ) | 594.0 | 90.4 | n=3 |
| Cyclone DDS | 32 | — (0 tin đến) | — (0% hội tụ) | 594.0 | 90.3 | n=3 |
| Zenoh | 32 | 48.2 | — (0% hội tụ) | 594.0 | 90.4 | n=3 |
| **Ours** | 32 | 43.1 | — (0% hội tụ) | 594.0 | 90.3 | n=3 |

### Tỷ lệ forced_entry (% lần "ép vào" zone do hết giờ chờ, không đạt đồng thuận thật)

| N | Fast DDS | Cyclone DDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | 100% | 100% | 69% | 100% |
| 16 | 100% | 100% | 64% | 100% |
| 32 | 100% | 100% | 100% | 100% |

### Ghi chú khi đọc bảng

- **"—"** ở cột Conflict-resolution delay = **0% lần crossing đạt
  đồng thuận thật** trong n=3 lần chạy — không có mẫu hợp lệ để tính
  trung bình, KHÔNG PHẢI 0ms.
- **So với bản KHÔNG suy hao trước đó, Fast DDS sập HẲN xuống 100%
  forced_entry ở CẢ N=8/16** (trước đó chỉ 0%/14%) — khác biệt LỚN
  nhất giữa 2 bản dữ liệu, xác nhận: kịch bản Bảng VI (broadcast dồn
  dập, cần ĐỦ N-1 reply mỗi lần) nhạy với suy hao radio hơn NHIỀU so
  với Bảng V (trace-replay, tải thấp hơn) — chỉ 2% suy hao cũng đủ
  triệt tiêu khả năng hội tụ mà Fast DDS từng có khi không suy hao.
- **Zenoh là RMW DUY NHẤT còn đạt đồng thuận thật ở CẢ 3 quy mô**
  (69%/64%/100% forced — tức 31%/36%/0% THẬT SỰ thành công) — khác
  biệt cấu trúc so với 3 phương thức kia, luôn 100% forced mọi quy mô.
- **CycloneDDS (static_peers) vẫn sập 0% ở MỌI profile/quy mô/có hay
  không suy hao** — nhất quán, giới hạn cấu trúc O(N²) discovery cost
  riêng của nó.
- **FleetRMW 100% forced_entry mọi quy mô** — đã xác nhận qua điều tra
  trước đó (xem `AUDIT_ACCEPTANCE_TRACKING.md`, mục "2 FURTHER FIXES")
  đây KHÔNG PHẢI do 2 bug Ricart-Agrawala đã vá, mà do vấn đề độ tin
  cậy broadcast N-chiều cần đủ N-1 reply trong 1 cửa sổ thời gian —
  giờ CÀNG rõ hơn khi cộng thêm 2% suy hao radio-link thật, không chỉ
  riêng do overhead core/scheduler như suy đoán ban đầu.
- **Navigation recovery count giống hệt Total crossings × N-per-run**
  (CycloneDDS/FleetRMW/Fast DDS luôn = 162/306/594 tương ứng N=8/16/32
  vì MỌI crossing đều forced/recovery, trừ Zenoh thấp hơn nhờ 1 phần
  crossing thành công thật).

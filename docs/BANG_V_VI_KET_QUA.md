# Bảng V và Bảng VI — Kết quả đo thật

Tài liệu này gom lại 2 bảng kết quả cuối cùng (dữ liệu đo thật qua
harness Docker + ns-3, không phải suy đoán) để tiện đối chiếu với bài
báo. Chi tiết phương pháp, quá trình debug, và các phát hiện phụ trợ
nằm ở [`docs/AUDIT_ACCEPTANCE_TRACKING.md`](AUDIT_ACCEPTANCE_TRACKING.md)
(tìm theo ngày `13/09/2026`).

## Bảng V — Đầy đủ 3 profile (Wi-Fi / LAN / 5G), N=16, n=3

| Method | Profile | p50 (ms) | p95 (ms) | p99 (ms) | Jitter (ms) | Delivery ratio | Repair amp. | Stale ratio | Queue HWM | Run/seed |
|---|---|---|---|---|---|---|---|---|---|---|
| Fast DDS | Wi-Fi | 8452.9 | 9641.9 | 9703.2 | 514.4 | 48.4% | N/A¹ | 100% | N/A² | seed42/run1-3 |
| Fast DDS | LAN | 376.1 | 1671.6 | 2147.7 | 616.4 | **86.3±0.0%** | N/A¹ | 62.5% | N/A² | n=3 |
| Fast DDS | 5G | — | — | — | — | **0%** | N/A¹ | — (0 tin) | N/A² | n=3 |
| CycloneDDS | Wi-Fi | — | — | — | — | 0.0% | N/A¹ | — (0 tin) | N/A² | seed42/run1-3 |
| CycloneDDS | LAN | 377.3 | 1672.7 | 2147.6 | 616.9 | **86.3±0.0%** | N/A¹ | 62.6% | N/A² | n=3 |
| CycloneDDS | 5G | — | — | — | — | **0%** | N/A¹ | — (0 tin) | N/A² | n=3 |
| Zenoh | Wi-Fi | 2692.5 | 4298.5 | 4812.3 | 842.5 | 48.5% | N/A¹ | 99.9% | N/A² | seed42/run1-3 |
| Zenoh | LAN | 372.5 | 1749.1 | 2147.8 | 627.1 | 52.0±35.3% | N/A¹ | 62.5% | N/A² | n=3 |
| Zenoh | 5G | — | — | — | — | **0%** | N/A¹ | — (0 tin) | N/A² | n=3 |
| **Ours (FleetRMW)** | Wi-Fi | 5515.9 | 9947.6 | 10907.0 | 3071.0 | 27.9% | 0.0% | 99.0% | N/A² | seed42/run1-3 |
| **Ours (FleetRMW)** | LAN | 698.7 | 1794.8 | 2203.3 | 605.2 | **65.8±0.1%** | 0.000 | 81.7% | N/A² | n=3 |
| **Ours (FleetRMW)** | 5G | 9765.9 | 10427.0 | 11110.1 | 324.0 | **1.9%** | 0.000 | 100% | N/A² | n=3 |

¹ Chỉ FleetRMW đo được (3 RMW kia là hộp đen, không có introspection qua harness này).
² Chưa có instrumentation Queue HWM cho bất kỳ RMW nào.

### Phụ lục — 5G theo quy mô N=8/16/32 (Wi-Fi/LAN chưa đo ở các N này)

| N | Fast DDS | CycloneDDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | 23.6% (p50=4990ms) | 0% | 34.1% (p50=5896ms) | 39.8% (p50=4437ms) |
| 16 | 0% | 0% | 0% | 1.9% (p50=9766ms) |
| 32 | 0% | 0% | 0% | 0% |

### Nhận xét chính

- **Xếp hạng dung lượng mạng (cùng N=16)**: LAN (lý tưởng) > Wi-Fi
  (nghẽn kênh vừa phải) > 5G (sập gần hoàn toàn ở cấu hình PHY đơn
  giản 1 gNB/1 bandwidth-part 20MHz/numerology 1 đang dùng).
- **FleetRMW là RMW duy nhất còn sống ở cả 3 profile**, kể cả 5G —
  vì là RMW duy nhất không cần bước discovery (static mode), không bị
  chặn bởi độ trễ round-trip cực cao làm hỏng handshake của 3 RMW kia.
- **Trên LAN lý tưởng, FleetRMW (65.8%) thua cả Fast DDS/CycloneDDS
  (86.3%)** — khi kênh không còn là nút thắt, chính overhead xử lý nội
  bộ của FleetRMW (mã hoá JSON, transport riêng) trở thành nút thắt.
- **Stale ratio ~99-100% ở Wi-Fi và 5G** (mọi RMW có giao tin) —
  "delivery ratio" một mình nó gây hiểu lầm nghiêm trọng: dù có tin
  đến, phần lớn đã trễ deadline, vô dụng cho điều khiển robot thời
  gian thực.
- **5G sập dung lượng SỚM HƠN NHIỀU so với Wi-Fi/LAN** ở cùng N — xác
  nhận bằng thử nghiệm tăng gấp 3 thời gian mô phỏng cho kết quả giống
  hệt (rớt gói thật ở lớp MAC/radio, không phải do thiếu thời gian
  chờ).

## Bảng VI — Chỉ số điều phối và hoàn thành nhiệm vụ (N=8/16/32, n=3)

Kịch bản: mô phỏng loại trừ tương hỗ Ricart-Agrawala — nhiều robot
tranh chấp 1 zone/đường đi chung, chạy trên chính hạ tầng RMW/Wi-Fi đã
dùng cho Bảng IV/V.

| Method | N robots | Coordination update age (ms) | Conflict-resolution delay (ms) | Navigation recovery count | Task completion time (s) | Scenario/seed |
|---|---|---|---|---|---|---|
| Fast DDS | 8 | 2398.1 | 7544.2 | 27.3 | 25.7 | n=3 |
| Cyclone DDS | 8 | — (0 tin đến) | — (0% hội tụ) | 154.3 | 90.6 | n=3 |
| Zenoh | 8 | 107.3 | 8248.6 | 72.0 | 90.3 | n=3 |
| **Ours** | 8 | 8129.9 | — (0% hội tụ) | 161.7 | 90.3 | n=3 |
| Fast DDS | 16 | 17420.6 | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| Cyclone DDS | 16 | — (0 tin đến) | — (0% hội tụ) | 306.0 | 90.3 | n=3 |
| Zenoh | 16 | 6818.4 | 33065.3 | 305.3 | 90.3 | n=3 |
| **Ours** | 16 | 23799.9 | — (0% hội tụ) | 305.7 | 90.4 | n=3 |
| Fast DDS | 32 | 21343.0 | — (0% hội tụ) | 594.0 | 90.4 | n=3 |
| Cyclone DDS | 32 | — (0 tin đến) | — (0% hội tụ) | 594.0 | 90.4 | n=3 |
| Zenoh | 32 | 21362.6 | — (0% hội tụ) | 594.0 | 90.3 | n=3 |
| **Ours** | 32 | 36802.2 | — (0% hội tụ) | 584.3 | 90.5 | n=3 |

### Tỷ lệ forced_entry (% lần "ép vào" zone do hết giờ chờ, không đạt đồng thuận thật)

| N | Fast DDS | Cyclone DDS | Zenoh | FleetRMW |
|---|---|---|---|---|
| 8 | **0%** | 100% | 15% | 100% |
| 16 | 100% | 100% | 95% | 100% |
| 32 | 100% | 100% | 100% | 100% |

### Ghi chú khi đọc bảng

- **"—"** ở cột Conflict-resolution delay = **0% lần crossing đạt
  đồng thuận thật** trong n=3 lần chạy — không có mẫu hợp lệ để tính
  trung bình, KHÔNG PHẢI 0ms.
- **"—" ở Cyclone DDS (Coordination update age)** = 0 tin nhắn nào
  từng đến ở MỌI quy mô — khớp vấn đề discovery O(N²) đã ghi nhận
  trước đó với CycloneDDS static_peers (Bảng IV/V).
- **Navigation recovery count tăng gần tuyến tính theo N** (27→306→594
  cho Fast DDS) — phản ánh đúng độ khó tăng theo cấp số nhân của bài
  toán đồng thuận N-1 reply khi N tăng.
- **Fast DDS hội tụ hoàn hảo ở N=8** (đã xác minh không phải bug rỗng
  — coordination_message_ages_ms thật ~2.4-8.3s, số lần retry giảm dần
  2→1→0 qua các crossing) nhưng sập ngay từ N=16 giống mọi phương thức
  khác.
- **Từ N=16 trở lên, cả 4 phương thức đều sập ~100%** — đã xác nhận
  qua sensitivity test riêng (tăng `reply-timeout-s` từ 5s→10s→20s làm
  forced_entry TĂNG chứ không giảm, 50%→65%→70%) rằng đây là thất bại
  đồng thuận thật do mất gói Wi-Fi, không phải do thiếu thời gian chờ.
- Trong lúc chạy batch, phát hiện và sửa 1 crash thật ở N=32 FleetRMW
  (`errno=105`, tràn buffer UDP của OS khi 32 endpoint cùng broadcast)
  — xem chi tiết trong `AUDIT_ACCEPTANCE_TRACKING.md`.

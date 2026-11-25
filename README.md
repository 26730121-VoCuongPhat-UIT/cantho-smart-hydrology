<div align="center">

# Cần Thơ Thủy Văn & Khí Quyển

**Ứng dụng web hỗ trợ người dân TP. Cần Thơ theo dõi thời tiết, thủy triều Sông Hậu theo thời gian thực, tìm đường né ngập và xem phản ánh hiện trường cộng đồng.**

[![Live Website](https://img.shields.io/badge/Live_Website-cantho--hydro--atlas.vercel.app-16231E?style=for-the-badge&logo=vercel&logoColor=white)](https://cantho-hydro-atlas.vercel.app)
[![License: MIT](https://img.shields.io/badge/License-MIT-43534B?style=for-the-badge)](LICENSE)

[**Trải nghiệm trực tiếp**](https://cantho-hydro-atlas.vercel.app) · [**Mở Bản đồ Né ngập**](https://cantho-hydro-atlas.vercel.app/?mode=flood&from=dhct_khu2&to=ben_ninh_kieu&veh=scooter)

<br/><br/>

<a href="https://cantho-hydro-atlas.vercel.app">
  <img src="docs/screenshots/hero-banner.png" alt="Cần Thơ Thủy Văn & Khí Quyển — Thời tiết, Thủy triều Sông Hậu & Bản đồ Né ngập" width="100%" />
</a>

</div>

---

## Giới Thiệu

Đây là một mini project cá nhân được xây dựng với mong muốn mang lại công cụ tra cứu nhanh, dễ dùng trên cả máy tính lẫn điện thoại cho người dân và người tham gia giao thông tại TP. Cần Thơ — đặc biệt vào những ngày mưa lớn kết hợp triều cường dâng cao.

---

## Hướng Dẫn Sử Dụng

Ứng dụng gồm 3 mục chính ngay trên thanh điều hướng phía trên (có nút chuyển đổi giao diện **Sáng / Ban đêm** ở góc phải):

### 01 · Theo dõi Thời tiết & Thủy triều (`Hôm nay`)
- Chọn nhanh **6 Quận/Huyện** (*Ninh Kiều, Bình Thủy, Cái Răng, Ô Môn, Thốt Nốt, Phong Điền*) để xem nhiệt độ, độ ẩm, chỉ số chất lượng không khí (`US AQI`, `PM2.5`), chỉ số `UV` và giờ mặt trời mọc/lặn.
- Quan sát trực tiếp biểu đồ mặt cắt so sánh **mực nước triều Sông Hậu** với **cốt nền từng khu vực** và các ngưỡng báo động I/II/III để biết mặt đường đang cao hơn hay thấp hơn nước sông bao nhiêu mét.
- Xem dự báo theo **24 giờ**, **7 ngày** cùng đỉnh triều cao nhất trong ngày và lưu ý đi lại/giao thông thủy ở chân trang.

### 02 · Bản đồ & Tìm đường Né ngập (`Tìm đường né ngập`)
- **Chọn phương tiện đang đi:** *Xe tay ga*, *Xe số/Côn tay*, *Ô tô 4 chỗ Sedan* hoặc *Ô tô gầm cao SUV/Bán tải* để hệ thống tính ngưỡng ngập an toàn phù hợp.
- **Chọn điểm đi (A) và điểm đến (B):** Gõ tên địa điểm quen thuộc ở Cần Thơ (trường học, bệnh viện, siêu thị, cầu, bến xe, tên đường...), bấm nút đổi chiều A ↔ B, chọn **`Vị trí của tôi`**, hoặc bấm **`Chấm A/B trên map`** rồi kéo thả trực tiếp ghim A/B trên bản đồ.
- **So sánh & đi theo lộ trình an toàn:** Xem đối chiếu giữa **Đường an toàn** và **Đường ngắn nhất**, tham khảo gợi ý khung giờ xuất hành (`Đi ngay`, `+1h`, `+2h`, `+3h`), bật thanh **Giả lập mùa triều cường / mưa lớn** để kiểm tra trước các kịch bản ngập, hoặc bấm **`Google Maps`** để mở lộ trình né ngập sang ứng dụng Google Maps trên điện thoại.

### 03 · Bảng tin Cộng đồng & Camera Giao lộ (`Báo ngập & Camera`)
- **Chia sẻ tình hình đường phố:** Chọn trạng thái (*Ngập sâu*, *Ngập nhẹ*, *Kẹt xe*, *Mưa lớn*, *Khô thoáng*), nhập mực nước ước lượng (cm), chọn giao lộ hoặc chấm vị trí trên bản đồ và đính kèm ảnh chụp thực tế để cảnh báo mọi người.
- **Xác nhận thông tin nhanh:** Bấm **`+1 Xác nhận`**, **`Đã rút nước`** hoặc bình luận cập nhật thêm dưới bài đăng; các tin báo sẽ tự động hết hiệu lực khi nước rút hoặc quá thời gian quy định. Có sẵn nút **`Xem dữ liệu mẫu`** để bạn xem thử cách hiển thị trên bản đồ mà không ảnh hưởng đến dữ liệu thật.
- **Theo dõi 8 Trạm Camera Giao lộ (`CAM 01` – `CAM 08`):** Bấm chọn từng nút giao trọng điểm để xem mực nước ước lượng, ảnh cộng đồng mới nhất quanh ngã tư hoặc mở thẳng **Google Street View** tại góc đường đó.

---

## Giao Diện Thực Tế

<table>
  <tr>
    <td width="50%">
      <strong>01 · Thời tiết &amp; Thủy triều — Chế độ Sáng</strong><br/><br/>
      <img src="docs/screenshots/01-weather-light.png" alt="Giao diện theo dõi thời tiết và mực nước Sông Hậu tại Cần Thơ ở chế độ sáng" />
    </td>
    <td width="50%">
      <strong>02 · Thời tiết &amp; Thủy triều — Chế độ Ban đêm</strong><br/><br/>
      <img src="docs/screenshots/02-weather-dark.png" alt="Giao diện theo dõi thời tiết và thủy triều Cần Thơ ở chế độ ban đêm" />
    </td>
  </tr>
  <tr>
    <td width="50%">
      <strong>03 · Bản đồ Tìm đường Né ngập A/B &amp; Google Maps</strong><br/><br/>
      <img src="docs/screenshots/03-flood-routing.png" alt="Bản đồ tìm đường né ngập hiển thị đoạn ngập đỏ, lộ trình an toàn xanh và nút mở Google Maps" />
    </td>
    <td width="50%">
      <strong>04 · Bảng tin Cộng đồng &amp; 8 Trạm Camera Giao lộ</strong><br/><br/>
      <img src="docs/screenshots/04-community-cameras.png" alt="Bảng tin phản ánh đường phố cộng đồng và 8 điểm quan sát camera giao lộ tại Cần Thơ" />
    </td>
  </tr>
  <tr>
    <td colspan="2" align="center">
      <strong>05 · Giao diện Điện thoại (390×844)</strong><br/><br/>
      <img src="docs/screenshots/05-mobile-view.png" width="320" alt="Giao diện trang web hiển thị gọn gàng trên màn hình điện thoại" />
    </td>
  </tr>
</table>

---

## Lời Cảm Ơn

Xin gửi lời cảm ơn chân thành đến:
- Các nguồn dữ liệu mở và nền tảng bản đồ miễn phí như **Open-Meteo**, **GloFAS**, **OpenStreetMap** và **Leaflet** đã cung cấp dữ liệu thời tiết, lưu lượng dòng chảy và bản đồ đường phố Cần Thơ phục vụ cho dự án cá nhân này.
- Mọi người dùng đã ghé thăm, trải nghiệm thử và đóng góp các phản ánh thực tế giúp ích cho việc di chuyển an toàn tại Cần Thơ.

---

## Tác Giả & Liên Hệ

- **Tác giả:** `vcphat`
- **Email:** [26730121@ms.uit.edu.vn](mailto:26730121@ms.uit.edu.vn)
- **GitHub:** [https://github.com/vocuongphat06-collab](https://github.com/vocuongphat06-collab)
- **Facebook:** [https://www.facebook.com/zokuongfak/](https://www.facebook.com/zokuongfak/)
- **Giấy phép:** [MIT License](LICENSE)

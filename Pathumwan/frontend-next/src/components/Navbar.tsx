"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import { useState } from "react";
import {
  Home,
  BarChart3,
  Layers,
  Camera,
  SlidersHorizontal,
  User,
} from "lucide-react";

const navItems = [
  { href: "/dashboard", label: "หน้าหลัก", icon: Home },
  { href: "/statistics", label: "สถิติ", icon: BarChart3 },
  { href: "/density", label: "ความหนาแน่น", icon: Layers },
];

const adminItems = [
  { href: "/cameras", label: "กล้องจราจร", icon: Camera },
  { href: "/control", label: "ควบคุม", icon: SlidersHorizontal },
];

export default function Navbar() {
  const pathname = usePathname();
  const router = useRouter();
  const { user, logout } = useAuth();
  const [showProfile, setShowProfile] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editUsername, setEditUsername] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPassword, setEditPassword] = useState("");
  const [editMsg, setEditMsg] = useState("");

  const profileLabel = user?.role === "admin" ? "เจ้าหน้าที่" : "ผู้ใช้ทั่วไป";

  return (
    <>
      <nav className="fixed top-0 left-0 right-0 z-[9999] h-14 bg-[#5ba8e0] flex items-center px-4 gap-1 shadow-md">
        {/* Logo */}
        <Link
          href="/dashboard"
          className="flex items-center gap-2 mr-4 shrink-0"
        >
          <span className="text-white font-bold text-xl tracking-wide">
            Traffix<span className="text-yellow-300">Flow</span>
          </span>
          <span className="text-lg">🚦</span>
        </Link>

        {/* Nav tabs */}
        <div className="flex items-center gap-0.5 overflow-x-auto">
          {navItems.map((item) => {
            const active = pathname === item.href;
            return (
              <Link
                key={item.href}
                href={item.href}
                className={`flex items-center gap-1.5 px-4 py-2 text-sm whitespace-nowrap transition-colors rounded-md ${
                  active
                    ? "bg-white/25 text-white font-semibold"
                    : "text-white/90 hover:bg-white/15"
                }`}
              >
                <item.icon size={16} />
                {item.label}
              </Link>
            );
          })}
          {user?.role === "admin" &&
            adminItems.map((item) => {
              const active = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center gap-1.5 px-4 py-2 text-sm whitespace-nowrap transition-colors rounded-md ${
                    active
                      ? "bg-white/25 text-white font-semibold"
                      : "text-white/90 hover:bg-white/15"
                  }`}
                >
                  <item.icon size={16} />
                  {item.label}
                </Link>
              );
            })}
        </div>

        {/* Profile / Right side */}
        <div className="ml-auto flex items-center gap-2 shrink-0 relative">
          <button
            onClick={() => setShowProfile(!showProfile)}
            className="flex items-center gap-1.5 text-sm text-white/90 hover:text-white transition-colors px-3 py-2 rounded-md hover:bg-white/15"
          >
            <span>{profileLabel}</span>
            <User size={18} />
          </button>

          {/* Profile dropdown */}
          {showProfile && (
            <div className="absolute top-12 right-0 bg-white rounded-xl shadow-xl border border-gray-200 p-5 w-80 z-[10000]">
              <div className="mb-4 flex items-center justify-between gap-3">
                <h3 className="text-[#4a90d9] font-bold">
                  {user?.role === "admin" ? "บัญชีเจ้าหน้าที่" : "บัญชีผู้ใช้ทั่วไป"}
                </h3>
                <button
                  type="button"
                  aria-label="ปิด"
                  onClick={() => {
                    setEditing(false);
                    setEditMsg("");
                    setShowProfile(false);
                  }}
                  className="rounded-full p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M18 6 6 18" />
                    <path d="m6 6 12 12" />
                  </svg>
                </button>
              </div>

              {!editing ? (
                <>
                  <div className="space-y-3">
                    <div className="bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-600">
                      <span className="text-[10px] text-gray-400 block">ชื่อผู้ใช้</span>
                      {user?.username}
                    </div>
                    <div className="bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-600">
                      <span className="text-[10px] text-gray-400 block">บทบาท</span>
                      {user?.role === "admin" ? "เจ้าหน้าที่" : "ผู้ใช้ทั่วไป"}
                    </div>
                  </div>
                  <button
                    onClick={() => {
                      setEditing(true);
                      setEditUsername(user?.username || "");
                      setEditEmail(user?.email || "");
                      setEditPassword("");
                      setEditMsg("");
                    }}
                    className="text-sm text-[#4a90d9] hover:underline mt-3 block text-right w-full"
                  >
                    แก้ไขบัญชี
                  </button>
                </>
              ) : (
                <div className="space-y-3">
                  <div>
                    <label className="text-[10px] text-gray-400">ชื่อผู้ใช้</label>
                    <input
                      type="text"
                      value={editUsername}
                      onChange={(e) => setEditUsername(e.target.value)}
                      className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-gray-400">อีเมล</label>
                    <input
                      type="email"
                      value={editEmail}
                      onChange={(e) => setEditEmail(e.target.value)}
                      className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
                    />
                  </div>
                  <div>
                    <label className="text-[10px] text-gray-400">รหัสผ่านใหม่ (ไม่บังคับ)</label>
                    <input
                      type="password"
                      value={editPassword}
                      onChange={(e) => setEditPassword(e.target.value)}
                      className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
                      placeholder="กรอกเฉพาะกรณีต้องการเปลี่ยน"
                    />
                  </div>
                  {editMsg && (
                    <p className={`text-xs ${editMsg.includes("สำเร็จ") ? "text-green-600" : "text-red-500"}`}>
                      {editMsg}
                    </p>
                  )}
                  <div>
                    <button
                      onClick={async () => {
                        setEditMsg("");
                        try {
                          const { updateProfile } = await import("@/lib/api");
                          await updateProfile({
                            username: editUsername,
                            email: editEmail,
                            password: editPassword || undefined,
                          });
                          setEditMsg("บันทึกสำเร็จ");
                          setTimeout(() => { setEditing(false); setEditMsg(""); }, 1200);
                        } catch {
                          setEditMsg("เกิดข้อผิดพลาด");
                        }
                      }}
                      className="w-full bg-[#1e3a5f] hover:bg-[#162e4a] text-white text-sm py-2 rounded-lg transition-colors"
                    >
                      บันทึก
                    </button>
                  </div>
                </div>
              )}

              <div className="flex gap-2 mt-4">
                <button
                  onClick={() => {
                    logout();
                    setShowProfile(false);
                    router.push("/login");
                  }}
                  className="flex-1 bg-[#4a90d9] text-white rounded-lg py-2 text-sm font-medium hover:bg-[#3a7bc8] transition-colors"
                >
                  ออกจากระบบ
                </button>
                <button
                  onClick={async () => {
                    if (confirm("ต้องการยกเลิกบัญชีหรือไม่?")) {
                      try {
                        const { deactivateAccount } = await import("@/lib/api");
                        await deactivateAccount();
                      } catch { /* ignore */ }
                      logout();
                      setShowProfile(false);
                      router.push("/login");
                    }
                  }}
                  className="flex-1 bg-gray-200 text-gray-700 rounded-lg py-2 text-sm font-medium hover:bg-gray-300 transition-colors"
                >
                  ยกเลิกบัญชี
                </button>
              </div>
            </div>
          )}
        </div>
      </nav>
      {/* Overlay to close profile dropdown */}
      {showProfile && (
        <div
          className="fixed inset-0 z-[9998]"
          onClick={() => setShowProfile(false)}
        />
      )}
    </>
  );
}

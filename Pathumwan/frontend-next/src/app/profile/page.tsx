"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import Navbar from "@/components/Navbar";
import ProtectedRoute from "@/components/ProtectedRoute";
import { useAuth } from "@/lib/auth";
import { updateProfile, deactivateAccount } from "@/lib/api";
import { useRouter } from "next/navigation";

const MapView = dynamic(() => import("@/components/MapView"), {
  ssr: false,
  loading: () => (
    <div className="flex-1 flex items-center justify-center bg-gray-100">
      <div className="animate-spin h-8 w-8 border-2 border-[#5ba8e0] border-t-transparent rounded-full" />
    </div>
  ),
});

export default function ProfilePage() {
  const { user, logout } = useAuth();
  const router = useRouter();
  const [editing, setEditing] = useState(false);
  const [username, setUsername] = useState(user?.username || "");
  const [email, setEmail] = useState(user?.email || "");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");

  async function handleSave() {
    setMsg("");
    try {
      await updateProfile({ username, email, password: password || undefined });
      setMsg("บันทึกสำเร็จ");
      setEditing(false);
      setPassword("");
    } catch {
      setMsg("เกิดข้อผิดพลาด");
    }
  }

  async function handleDeactivate() {
    if (!confirm("ยืนยันยกเลิกบัญชี?")) return;
    try {
      await deactivateAccount();
      logout();
      router.push("/login");
    } catch {
      setMsg("เกิดข้อผิดพลาด");
    }
  }

  const isAdmin = user?.role === "admin";

  return (
    <ProtectedRoute>
      <div className="h-screen flex flex-col">
        <Navbar />
        <main className="flex-1 relative pt-14">
          {/* Map background */}
          <MapView showVehicles={false} showCameras={false} showLights={false} />

          <div className="absolute inset-x-0 bottom-0 top-14 z-[1000] flex items-start justify-center px-4 pt-8 pb-6">
            <div className="w-full max-w-md bg-white rounded-2xl shadow-xl border border-gray-200 p-6">
              <div className="mb-5 flex items-start justify-between gap-4">
                <div>
                  <h2 className="text-lg font-bold text-[#1e3a5f] mb-1">
                    {isAdmin ? "บัญชีเจ้าหน้าที่" : "บัญชีผู้ใช้ทั่วไป"}
                  </h2>
                  <p className="text-xs text-gray-400">
                    {user?.role === "admin" ? "Admin" : "User"}
                  </p>
                </div>
                <button
                  onClick={() => router.back()}
                  className="rounded-full p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
                  aria-label="ปิด"
                >
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path d="M18 6 6 18" />
                    <path d="m6 6 12 12" />
                  </svg>
                </button>
              </div>

              <div className="space-y-3">
                <div>
                  <label className="text-xs text-gray-500">ชื่อผู้ใช้</label>
                  <input
                    type="text"
                    value={editing ? username : (username || user?.username || "")}
                    onChange={(e) => setUsername(e.target.value)}
                    disabled={!editing}
                    className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 border border-gray-200 disabled:opacity-60 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
                  />
                </div>
                <div>
                  <label className="text-xs text-gray-500">อีเมล</label>
                  <input
                    type="email"
                    value={editing ? email : (email || user?.email || "")}
                    onChange={(e) => setEmail(e.target.value)}
                    disabled={!editing}
                    className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 border border-gray-200 disabled:opacity-60 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
                  />
                </div>
                {editing && (
                  <div>
                    <label className="text-xs text-gray-500">
                      รหัสผ่านใหม่ (ไม่บังคับ)
                    </label>
                    <input
                      type="password"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      className="w-full bg-gray-50 rounded-lg px-3 py-2 text-sm text-gray-700 border border-gray-200 focus:outline-none focus:ring-1 focus:ring-[#5ba8e0]"
                      placeholder="กรอกเฉพาะกรณีต้องการเปลี่ยน"
                    />
                  </div>
                )}

                {msg && (
                  <p
                    className={`text-xs ${msg.includes("สำเร็จ") ? "text-green-600" : "text-red-500"}`}
                  >
                    {msg}
                  </p>
                )}

                {editing ? (
                  <div>
                    <button
                      onClick={handleSave}
                      className="w-full bg-[#1e3a5f] hover:bg-[#162e4a] text-white text-sm py-2 rounded-lg transition-colors"
                    >
                      บันทึก
                    </button>
                  </div>
                ) : (
                  <button
                    onClick={() => {
                      setUsername(username || user?.username || "");
                      setEmail(email || user?.email || "");
                      setPassword("");
                      setMsg("");
                      setEditing(true);
                    }}
                    className="w-full bg-[#5ba8e0] hover:bg-[#4a97cf] text-white text-sm py-2 rounded-lg transition-colors"
                  >
                    แก้ไขบัญชี
                  </button>
                )}

                <hr className="border-gray-200" />

                <button
                  onClick={() => {
                    logout();
                    router.push("/login");
                  }}
                  className="w-full bg-gray-100 hover:bg-gray-200 text-gray-700 text-sm py-2 rounded-lg transition-colors"
                >
                  ออกจากระบบ
                </button>

                <button
                  onClick={handleDeactivate}
                  className="w-full text-red-500 hover:bg-red-50 text-sm py-2 rounded-lg transition-colors"
                >
                  ยกเลิกบัญชี
                </button>
              </div>
            </div>
          </div>
        </main>
      </div>
    </ProtectedRoute>
  );
}

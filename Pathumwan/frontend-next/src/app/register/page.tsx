"use client";

import { useState, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { register } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function RegisterPage() {
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const { setAuth } = useAuth();
  const router = useRouter();

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError("");

    if (password !== confirm) {
      setError("รหัสผ่านไม่ตรงกัน");
      return;
    }
    if (password.length < 6) {
      setError("รหัสผ่านต้องมีอย่างน้อย 6 ตัวอักษร");
      return;
    }

    setLoading(true);
    try {
      const res = await register(username, email, password);
      setAuth(res.data.token, res.data.user);
      router.push("/dashboard");
    } catch (err: unknown) {
      const msg =
        (err as { response?: { data?: { error?: string; message?: string } } })
          ?.response?.data?.error ||
        (err as { response?: { data?: { error?: string; message?: string } } })
          ?.response?.data?.message ||
        "สมัครสมาชิกไม่สำเร็จ";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-white">
      {/* Top bar with logo */}
      <div className="h-14 bg-[#5ba8e0] flex items-center px-4 shadow-md">
        <Link href="/login" className="flex items-center gap-2">
          <span className="text-white font-bold text-xl">
            Traffix<span className="text-yellow-300">Flow</span>
          </span>
          <span className="text-lg">🚦</span>
        </Link>
      </div>

      {/* Form */}
      <div className="flex items-center justify-center pt-16 px-4">
        <div className="w-full max-w-lg bg-[#b8d8eb] rounded-2xl p-8">
          <h2 className="text-2xl font-bold text-[#4a90d9] text-center mb-6">
            สร้างบัญชีผู้ใช้
          </h2>

          <form onSubmit={handleSubmit} className="space-y-4">
            {error && (
              <p className="text-red-600 text-sm bg-red-100 rounded-lg px-3 py-2">
                {error}
              </p>
            )}

            <input
              type="text"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#4a90d9]/30 border border-gray-200"
              placeholder="ชื่อผู้ใช้งาน"
            />

            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#4a90d9]/30 border border-gray-200"
              placeholder="อีเมล"
            />

            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#4a90d9]/30 border border-gray-200"
              placeholder="รหัสผ่าน"
            />

            <input
              type="password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="w-full bg-white rounded-lg px-4 py-3 text-gray-700 placeholder-gray-400 focus:outline-none focus:ring-2 focus:ring-[#4a90d9]/30 border border-gray-200"
              placeholder="ยืนยันรหัสผ่าน"
            />

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-[#1e3a5f] hover:bg-[#162e4a] disabled:opacity-50 text-white font-medium rounded-lg py-3 transition-colors text-lg mt-2"
            >
              {loading ? "กำลังลงทะเบียน..." : "ลงทะเบียน"}
            </button>
          </form>

          <p className="text-center text-sm text-gray-600 mt-4">
            มีบัญชีแล้ว?{" "}
            <Link href="/login" className="text-[#4a90d9] hover:underline font-medium">
              เข้าสู่ระบบ
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
